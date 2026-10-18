"""
下载目录空间清理（DownloadsCleaner，MoviePilot V3 插件）

触发条件：监控盘可用空间低于阈值（默认 300 GiB）。
清理范围：仅配置的下载目录子树（默认 E:/Downloads），天然排除 SSD / 保种专区等其它目录。
目标条件：任务已完成 + 做种时长 >= 阈值 + 不带保护标签 + 分享率/大小/完成时间达标
          + 已确认媒体入库（MP 整理记录成功，或额外白名单）。
清理动作：删除下载器任务（可选连同本地文件、MP 历史记录、空目录回收）；
          按做种时长从长到短删，直到空间回到目标线或达到单轮上限。

设计参考：
- MoviePilot V3 官方插件开发规范（app.sdk.* 导入）；
- H&R助手（hitandrun）的服务注册与详情页模式；
- QbUploadLimiter（插件市场）的下载器/通知渠道自动识别与入库判断。
"""

import os
import shutil
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.oper.transferhistory import TransferHistoryOper
from app.sdk.config import settings
from app.sdk.logging import logger
from app.sdk.plugin import _PluginBase
from app.sdk.services import DownloaderHelper, ServiceConfigHelper
from app.schemas import NotificationType
from app.schemas.types import NotificationChannel

BS = chr(92)  # 反斜杠，避免源码转义歧义
GIB = 1024 ** 3

# 配置里通知渠道名 -> SDK 枚举
_CHANNEL_MAP = {
    "Wechat": NotificationChannel.Wechat,
    "Feishu": NotificationChannel.Feishu,
    "WechatClawBot": NotificationChannel.WechatClawBot,
    "Telegram": NotificationChannel.Telegram,
    "Slack": NotificationChannel.Slack,
    "Discord": NotificationChannel.Discord,
    "DingTalk": NotificationChannel.DingTalk,
    "SynologyChat": NotificationChannel.SynologyChat,
    "VoceChat": NotificationChannel.VoceChat,
    "Web": NotificationChannel.Web,
    "WebPush": NotificationChannel.WebPush,
    "QQ": NotificationChannel.QQ,
}


class DownloadsCleaner(_PluginBase):
    """按磁盘可用空间阈值，自动清理下载目录中已入库、做种超时的下载任务。"""

    plugin_name = "下载目录空间清理"
    plugin_desc = ("磁盘可用空间低于阈值时，自动清理下载目录中已入库、做种超时的下载任务；"
                   "支持目录白名单、保护标签、完成保护、分享率门槛、通知渠道、两阶段删除与详情统计。")
    plugin_icon = "clean.png"
    plugin_version = "2.0.0"
    plugin_author = "yxb2018"
    plugin_config_prefix = "downloadscleaner_"
    plugin_order = 29
    auth_level = 1

    LOG_TAG = "[下载目录空间清理] "

    # ---------------- 运行态配置（init_plugin 收到配置后覆盖） ----------------

    _enabled = False
    _dry_run = True
    _onlyonce = False
    _notify = True
    _notify_channels: List[str] = []
    _cron = "0 */6 * * *"
    _monitor_path = "E:/"
    _threshold_free_gb = 300.0
    _target_free_gb = 350.0
    _downloaders: List[str] = []          # 空 = 全部已配置下载器
    _clean_dirs: List[str] = ["E:/Downloads"]
    _block_keywords: List[str] = []
    _min_seeding_days = 15
    _protect_tags: List[str] = ["保种", "H&R"]
    _protect_recent_days = 7
    _min_ratio = 0.0
    _min_size_gb = 0.0
    _require_library = True
    _verified_extra_text = ""
    _delete_files = True
    _delete_history = True
    _clean_empty_dirs = False
    _tag_then_delete = False
    _delete_tag = "待清理"
    _max_delete_per_run = 30

    # 立即运行一次的自有调度器（框架托管周期任务之外的一次性任务）
    _once_scheduler: Optional[BackgroundScheduler] = None

    # ---------------------------------------------------------------- 生命周期

    def init_plugin(self, config: dict = None):
        # 防止保存配置重入 init_plugin 时残留旧的一次性调度器
        self._stop_once_scheduler()

        if not config:
            return

        self._enabled = bool(config.get("enabled"))
        self._dry_run = bool(config.get("dry_run", True))
        self._onlyonce = bool(config.get("onlyonce"))
        self._notify = bool(config.get("notify", True))
        self._notify_channels = self._to_list(config.get("notify_channels"))
        self._cron = str(config.get("cron") or self._cron).strip()
        self._monitor_path = str(config.get("monitor_path") or self._monitor_path).strip() or self._monitor_path
        self._threshold_free_gb = self._to_float(config.get("threshold_free_gb"), 300.0)
        self._target_free_gb = self._to_float(config.get("target_free_gb"), 350.0)
        self._downloaders = self._to_list(config.get("downloaders"))
        self._clean_dirs = self._to_list(config.get("clean_dirs")) or ["E:/Downloads"]
        self._block_keywords = self._to_list(config.get("block_keywords"))
        self._min_seeding_days = max(0, int(self._to_float(config.get("min_seeding_days"), 15)))
        self._protect_tags = self._to_list(config.get("protect_tags")) or ["保种", "H&R"]
        self._protect_recent_days = max(0, int(self._to_float(config.get("protect_recent_days"), 7)))
        self._min_ratio = max(0.0, self._to_float(config.get("min_ratio"), 0.0))
        self._min_size_gb = max(0.0, self._to_float(config.get("min_size_gb"), 0.0))
        self._require_library = bool(config.get("require_library", True))
        self._delete_files = bool(config.get("delete_files", True))
        self._delete_history = bool(config.get("delete_history", True))
        self._clean_empty_dirs = bool(config.get("clean_empty_dirs"))
        self._tag_then_delete = bool(config.get("tag_then_delete"))
        self._delete_tag = str(config.get("delete_tag") or self._delete_tag).strip() or self._delete_tag
        self._max_delete_per_run = max(1, int(self._to_float(config.get("max_delete_per_run"), 30)))

        # 额外白名单种子 Hash：表单文本 -> 插件数据（get_data 读取，跨会话生效）
        extra_text = str(config.get("verified_extra") or "")
        self._verified_extra_text = extra_text
        self.save_data("verified_extra", [h for h in self._to_list(extra_text)])

        # 捕获"立即运行一次"意图：后续规范化写回 onlyonce=False，
        # 若写回触发同进程重入 init_plugin，重入调用会把 self._onlyonce 置 False，
        # 提前捕获保证本轮调度意图不丢
        run_once = self._onlyonce

        # 规范化持久化：修正非法组合（目标线低于阈值等），避免表单回显异常值
        try:
            normalized = self._current_config()
            if normalized != config:
                self.update_config(normalized)
        except Exception:
            pass

        # 立即运行一次：3 秒后执行一轮（参考市场插件 onlyonce 模式）
        if run_once:
            self._onlyonce = False
            self._once_scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._once_scheduler.add_job(
                func=self._clean,
                trigger="date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                name=f"{self.plugin_name}立即执行",
            )
            try:
                self._once_scheduler.start()
            except Exception as err:
                logger.warning(f"{self.LOG_TAG}立即执行任务启动失败：{err}")

        logger.info(
            f"{self.LOG_TAG}初始化完成：启用={self._enabled} 演练={self._dry_run} "
            f"监控={self._monitor_path} 阈值={self._threshold_free_gb:.0f}GiB "
            f"目标={self._target_free_gb:.0f}GiB 目录={'/'.join(self._clean_dirs)} "
            f"做种>={self._min_seeding_days}天 周期={self._cron}"
        )

    def _current_config(self) -> Dict[str, Any]:
        """回读当前生效配置（用于规范化写回）。"""
        return {
            "enabled": self._enabled,
            "dry_run": self._dry_run,
            "onlyonce": False,
            "notify": self._notify,
            "notify_channels": self._notify_channels,
            "cron": self._cron,
            "monitor_path": self._monitor_path,
            "threshold_free_gb": self._threshold_free_gb,
            "target_free_gb": max(self._threshold_free_gb, self._target_free_gb),
            "downloaders": self._downloaders,
            "clean_dirs": self._clean_dirs,
            "block_keywords": self._block_keywords,
            "min_seeding_days": self._min_seeding_days,
            "protect_tags": self._protect_tags,
            "protect_recent_days": self._protect_recent_days,
            "min_ratio": self._min_ratio,
            "min_size_gb": self._min_size_gb,
            "require_library": self._require_library,
            "verified_extra": self._verified_extra_text,
            "delete_files": self._delete_files,
            "delete_history": self._delete_history,
            "clean_empty_dirs": self._clean_empty_dirs,
            "tag_then_delete": self._tag_then_delete,
            "delete_tag": self._delete_tag,
            "max_delete_per_run": self._max_delete_per_run,
        }

    def get_state(self) -> bool:
        return self._enabled

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def stop_service(self):
        """停止一次性调度器；周期任务由 get_service 框架托管。"""
        self._stop_once_scheduler()

    def _stop_once_scheduler(self):
        if self._once_scheduler:
            try:
                if self._once_scheduler.running:
                    self._once_scheduler.remove_all_jobs()
                    self._once_scheduler.shutdown(wait=False)
            except Exception as err:
                logger.debug(f"{self.LOG_TAG}停止一次性调度器异常：{err}")
            self._once_scheduler = None

    def get_service(self) -> List[Dict[str, Any]]:
        """注册周期检查服务，由 MP 插件框架托管调度。"""
        if not self._enabled or not self._cron:
            return []
        try:
            trigger = CronTrigger.from_crontab(self._cron)
        except Exception as err:
            logger.error(f"{self.LOG_TAG}cron 表达式无效 {self._cron}：{err}")
            return []
        return [
            {
                "id": "DownloadsCleaner",
                "name": self.plugin_name,
                "trigger": trigger,
                "func": self._clean,
                "kwargs": {},
            }
        ]

    # ---------------------------------------------------------------- 主流程

    def _clean(self):
        try:
            free_gb = self._free_gb(self._monitor_path)
        except Exception as err:
            logger.error(f"{self.LOG_TAG}读取监控盘空间失败 {self._monitor_path}：{err}")
            return
        if free_gb is None:
            logger.error(f"{self.LOG_TAG}监控盘路径不可用：{self._monitor_path}")
            return

        if free_gb >= self._threshold_free_gb:
            logger.info(
                f"{self.LOG_TAG}{self._monitor_path} 可用空间 {free_gb:.1f}GiB，"
                f"高于阈值 {self._threshold_free_gb:.0f}GiB，无需清理"
            )
            return

        mode = "演练" if self._dry_run else "执行"
        logger.info(
            f"{self.LOG_TAG}触发清理（{mode}）：{self._monitor_path} 可用 {free_gb:.1f}GiB "
            f"< 阈值 {self._threshold_free_gb:.0f}GiB，目标回到 {self._target_free_gb:.0f}GiB"
        )

        candidates = self._collect_candidates()
        self.save_data("candidates", candidates[:50])
        self.save_data("disk", {
            "monitor": self._monitor_path,
            "free_gb": round(free_gb, 1),
            "threshold": self._threshold_free_gb,
            "target": self._target_free_gb,
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        if not candidates:
            message = (
                f"{self._monitor_path} 可用空间 {free_gb:.1f}GiB 已低于阈值 "
                f"{self._threshold_free_gb:.0f}GiB，但没有符合清理条件的下载任务"
            )
            logger.warning(f"{self.LOG_TAG}{message}")
            self._notify(f"【{self.plugin_name}】\n{message}")
            return

        deleted: List[Dict[str, Any]] = []
        tagged: List[Dict[str, Any]] = []
        failed = 0
        freed_bytes = 0
        for item in candidates[: self._max_delete_per_run]:
            if not self._dry_run:
                current = self._free_gb(self._monitor_path)
                if current is not None and current >= self._target_free_gb:
                    logger.info(f"{self.LOG_TAG}可用空间已回到目标线 {self._target_free_gb:.0f}GiB，停止本轮清理")
                    break
            if self._tag_then_delete and self._delete_tag not in item["tags"]:
                if self._add_tag(item):
                    tagged.append(item)
                    logger.info(
                        f"{self.LOG_TAG}已打标签 [{item['name'][:60]}]（{self._delete_tag}，下轮删除）"
                    )
                else:
                    failed += 1
                time.sleep(0.3)
                continue
            if self._delete_one(item):
                deleted.append(item)
                freed_bytes += item["size"]
                logger.info(
                    f"{self.LOG_TAG}已删除任务 [{item['name'][:60]}] "
                    f"{item['size'] / GIB:.2f}GiB 做种 {item['seed_days']:.1f} 天"
                )
            else:
                failed += 1
                logger.warning(f"{self.LOG_TAG}删除失败 [{item['name'][:60]}]")
            time.sleep(0.3)

        if self._clean_empty_dirs and not self._dry_run:
            removed_dirs = self._recycle_empty_dirs()
            if removed_dirs:
                logger.info(f"{self.LOG_TAG}空目录回收 {removed_dirs} 个")

        self._record_run(deleted, freed_bytes, failed, tagged, mode)

        summary = (
            f"【{self.plugin_name}】{mode}完成\n"
            f"监控盘：{self._monitor_path}\n"
            f"删除任务：{len(deleted)} 个，释放 {freed_bytes / GIB:.2f}GiB"
        )
        if tagged:
            summary += f"\n标记待清理：{len(tagged)} 个（下轮删除）"
        if failed:
            summary += f"\n失败：{failed} 个"
        current = self._free_gb(self._monitor_path)
        if current is not None:
            summary += f"\n当前可用：{current:.1f}GiB（目标 {self._target_free_gb:.0f}GiB）"
        if deleted:
            summary += "\n明细：\n" + "\n".join(
                f"- {item['name'][:56]}（{item['size'] / GIB:.1f}GiB / 做种 {item['seed_days']:.0f} 天）"
                for item in deleted[:10]
            )
            if len(deleted) > 10:
                summary += f"\n- ……等共 {len(deleted)} 个"
        logger.info(summary.replace("\n", " | "))
        self._notify(summary)

    # ---------------------------------------------------------------- 候选收集

    def _collect_candidates(self) -> List[Dict[str, Any]]:
        roots = [self._norm_root(d) for d in self._clean_dirs if str(d).strip()]
        if not roots:
            logger.error(f"{self.LOG_TAG}未配置有效的清理目录，取消本轮")
            return []

        transfer_oper = TransferHistoryOper()
        verified_extra = {
            str(h).strip() for h in (self.get_data("verified_extra") or []) if str(h).strip()
        }
        protected = {t.strip() for t in self._protect_tags if t.strip()}
        block_words = [k.strip() for k in self._block_keywords if k.strip()]
        services = self._get_services()
        if not services:
            return []

        candidates: List[Dict[str, Any]] = []
        for downloader, service in services.items():
            instance = getattr(service, "instance", None)
            if not instance or not hasattr(instance, "get_torrents"):
                continue
            try:
                torrents, error_flag = instance.get_torrents(status="completed")
            except Exception as err:
                logger.warning(f"{self.LOG_TAG}读取 {downloader} 任务失败：{err}")
                continue
            if error_flag:
                logger.warning(f"{self.LOG_TAG}读取 {downloader} 任务返回错误标志，跳过该下载器")
                continue
            for torrent in torrents or []:
                item = self._match_torrent(
                    torrent, downloader, roots, protected, block_words,
                    transfer_oper, verified_extra,
                )
                if item:
                    candidates.append(item)

        # 做种时长最长的优先清理
        candidates.sort(key=lambda c: -c["seed_days"])
        logger.info(
            f"{self.LOG_TAG}候选扫描完成：命中 {len(candidates)} 个"
            f"（目录={'/'.join(roots)}，做种≥{self._min_seeding_days}天，"
            f"完成保护≥{self._protect_recent_days}天，排除标签={protected or '无'}，"
            f"要求入库={'开' if self._require_library else '关'}）"
        )
        return candidates

    def _get_services(self) -> Dict[str, Any]:
        """获取已配置且可连接的下载器实例。"""
        try:
            services = DownloaderHelper().get_services(
                name_filters=self._downloaders or None
            ) or {}
        except Exception as err:
            logger.error(f"{self.LOG_TAG}获取下载器列表失败：{err}")
            return {}
        active = {}
        for name, service in services.items():
            instance = getattr(service, "instance", None)
            if not instance:
                logger.warning(f"{self.LOG_TAG}下载器 [{name}] 实例不存在，跳过")
                continue
            try:
                if instance.is_inactive():
                    logger.warning(f"{self.LOG_TAG}下载器 [{name}] 未连接，跳过")
                    continue
            except Exception:
                pass
            active[name] = service
        if not active:
            logger.warning(f"{self.LOG_TAG}没有可连接的下载器，取消本轮")
        return active

    def _match_torrent(
        self,
        torrent: Any,
        downloader: str,
        roots: List[str],
        protected: Set[str],
        block_words: List[str],
        transfer_oper: TransferHistoryOper,
        verified_extra: Set[str],
    ) -> Optional[Dict[str, Any]]:
        try:
            torrent_hash = str(torrent.get("hash") or "").strip()
            if not torrent_hash:
                return None
            save_path = str(torrent.get("save_path") or "")
            name = str(torrent.get("name") or torrent_hash)
            if not self._in_roots(save_path, roots):
                return None
            # 路径黑名单
            if block_words:
                haystack = (save_path + " " + name).lower()
                if any(word.lower() in haystack for word in block_words):
                    return None
            tags = {t.strip() for t in str(torrent.get("tags") or "").split(",") if t.strip()}
            if tags & protected:
                return None
            if float(torrent.get("progress") or 0) < 1:
                return None
            seeding = int(torrent.get("seeding_time") or 0)
            if seeding < self._min_seeding_days * 86400:
                return None
            if self._protect_recent_days > 0:
                completion = int(torrent.get("completion_on") or 0)
                if completion > 0 and (time.time() - completion) < self._protect_recent_days * 86400:
                    return None
            if self._min_ratio > 0 and float(torrent.get("ratio") or 0) < self._min_ratio:
                return None
            size = int(torrent.get("size") or 0)
            if self._min_size_gb > 0 and size < self._min_size_gb * GIB:
                return None
            in_library = torrent_hash in verified_extra
            if self._require_library and not in_library:
                in_library = self._is_in_library(transfer_oper, verified_extra, torrent_hash)
                if not in_library:
                    return None
            return {
                "downloader": downloader,
                "hash": torrent_hash,
                "name": name,
                "save_path": save_path,
                "seed_days": seeding / 86400,
                "ratio": float(torrent.get("ratio") or 0),
                "size": size,
                "tags": sorted(tags),
                "in_library": in_library,
            }
        except Exception:
            return None

    # ---------------------------------------------------------------- 入库核验

    def _is_in_library(
        self, transfer_oper: TransferHistoryOper, verified_extra: Set[str], torrent_hash: str
    ) -> bool:
        """媒体已入库才算可删：整理记录成功，或已验证白名单。"""
        if torrent_hash in verified_extra:
            return True
        try:
            records = transfer_oper.list_by_hash(torrent_hash) or []
        except Exception as err:
            logger.warning(f"{self.LOG_TAG}查询整理记录失败 {torrent_hash[:12]}：{err}")
            return False
        for record in records:
            dest = str(getattr(record, "dest", "") or "")
            if getattr(record, "status", None) and dest.startswith("/Media/"):
                return True
        return False

    # ---------------------------------------------------------------- 删除动作

    def _delete_one(self, item: Dict[str, Any]) -> bool:
        try:
            service = DownloaderHelper().get_service(name=item["downloader"])
        except Exception as err:
            logger.warning(f"{self.LOG_TAG}获取下载器实例失败 {item['downloader']}：{err}")
            return False
        instance = getattr(service, "instance", None) if service else None
        if not instance or not hasattr(instance, "delete_torrents"):
            return False
        if self._dry_run:
            logger.info(
                f"{self.LOG_TAG}[演练] 将删除 [{item['name'][:60]}]"
                f"（delete_file={self._delete_files}）"
            )
            return True
        try:
            ok = bool(
                instance.delete_torrents(delete_file=self._delete_files, ids=[item["hash"]])
            )
        except Exception as err:
            logger.error(f"{self.LOG_TAG}删除任务失败 [{item['name'][:60]}]：{err}")
            return False
        if ok and self._delete_history:
            self._clean_history(item["hash"], item["name"])
        return ok

    def _add_tag(self, item: Dict[str, Any]) -> bool:
        """两阶段删除：先打标签，下一轮才删。"""
        try:
            service = DownloaderHelper().get_service(name=item["downloader"])
            instance = getattr(service, "instance", None) if service else None
            if not instance or not hasattr(instance, "set_torrents_tag"):
                return False
            return bool(instance.set_torrents_tag(ids=item["hash"], tags=[self._delete_tag]))
        except Exception as err:
            logger.warning(f"{self.LOG_TAG}打标签失败 [{item['name'][:60]}]：{err}")
            return False

    def _clean_history(self, torrent_hash: str, name: str):
        """同步清理 MP 整理/下载历史记录，避免删除任务后残留幽灵记录。"""
        try:
            from app.db.oper.downloadhistory import DownloadHistoryOper
            transfer_oper = TransferHistoryOper()
            for record in transfer_oper.list_by_hash(torrent_hash) or []:
                try:
                    transfer_oper.delete(record.id)
                except Exception as err:
                    logger.warning(f"{self.LOG_TAG}清理整理记录失败 [{name[:40]}]：{err}")
            dl_oper = DownloadHistoryOper()
            history = dl_oper.get_by_hash(torrent_hash)
            if history:
                dl_oper.delete_history(history.id)
        except Exception as err:
            logger.warning(f"{self.LOG_TAG}清理下载历史失败 [{name[:40]}]：{err}")

    def _recycle_empty_dirs(self) -> int:
        """回收清理目录子树下的空目录（自底向上，限量防护）。"""
        removed = 0
        roots = [self._norm_root(d) for d in self._clean_dirs if str(d).strip()]
        for root in roots:
            try:
                walker = list(os.walk(root, topdown=False))
            except Exception:
                continue
            root_norm = os.path.normpath(root).upper()
            for dirpath, _dirnames, _filenames in walker:
                if os.path.normpath(dirpath).upper() == root_norm:
                    continue
                try:
                    if not os.listdir(dirpath):
                        os.rmdir(dirpath)
                        removed += 1
                        if removed >= 200:
                            return removed
                except Exception:
                    continue
        return removed

    # ---------------------------------------------------------------- 统计与通知

    def _record_run(self, deleted, freed_bytes, failed, tagged, mode):
        statistic = self.get_data("statistic") or {}
        statistic.update({
            "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_deleted": len(deleted),
            "last_freed_gb": round(freed_bytes / GIB, 2),
            "last_failed": failed,
            "last_tagged": len(tagged),
            "last_mode": mode,
            "total_deleted": int(statistic.get("total_deleted") or 0) + len(deleted),
            "total_freed_gb": round(float(statistic.get("total_freed_gb") or 0) + freed_bytes / GIB, 2),
        })
        self.save_data("statistic", statistic)

        if deleted:
            history = self.get_data("history") or []
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for item in deleted:
                history.append({
                    "name": item["name"],
                    "size_gb": round(item["size"] / GIB, 2),
                    "seed_days": round(item["seed_days"], 1),
                    "save_path": item["save_path"],
                    "downloader": item["downloader"],
                    "mode": mode,
                    "time": now,
                })
            self.save_data("history", history[-100:])

    def _notify(self, text: str):
        if not self._notify:
            return
        channels = [c for c in (_CHANNEL_MAP.get(ch) for ch in self._notify_channels) if c]
        try:
            self.post_message(
                channel=channels[0] if channels else None,
                mtype=NotificationType.SiteMessage,
                title=f"【{self.plugin_name}】",
                text=text,
            )
        except Exception as err:
            logger.debug(f"{self.LOG_TAG}通知发送失败：{err}")

    # ---------------------------------------------------------------- 工具

    def _free_gb(self, path: str) -> Optional[float]:
        try:
            return shutil.disk_usage(path).free / GIB
        except Exception:
            return None

    @staticmethod
    def _norm_root(root: Any) -> str:
        return str(root).strip().replace("/", BS).rstrip(BS).upper()

    @staticmethod
    def _in_roots(path: str, roots: List[str]) -> bool:
        norm = str(path or "").replace("/", BS).rstrip(BS).upper()
        return any(norm == root or norm.startswith(root + BS) for root in roots)

    @staticmethod
    def _to_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return [str(v).strip() for v in value if str(v).strip()]
        text = str(value)
        for sep in ("\r\n", "\n", ",", "，"):
            text = text.replace(sep, "\n")
        return [line.strip() for line in text.split("\n") if line.strip()]

    # ---------------------------------------------------------------- 配置表单

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        # 下载器下拉：MoviePilot 已配置并启用的下载器
        downloader_items = []
        try:
            for conf in ServiceConfigHelper.get_downloader_configs() or []:
                conf_name = getattr(conf, "name", "") or ""
                conf_type = getattr(conf, "type", "") or ""
                if conf_name and getattr(conf, "enabled", False):
                    downloader_items.append({"title": f"{conf_name}（{conf_type}）", "value": conf_name})
        except Exception as err:
            logger.warning(f"{self.LOG_TAG}读取下载器配置失败：{err}")

        # 通知渠道下拉：MoviePilot 已启用渠道
        notify_items = []
        try:
            for conf in ServiceConfigHelper.get_notification_configs() or []:
                conf_type = getattr(conf, "type", "") or ""
                conf_name = getattr(conf, "name", "") or conf_type
                if conf_type and {"title": conf_name, "value": conf_type} not in notify_items:
                    notify_items.append({"title": conf_name, "value": conf_type})
        except Exception as err:
            logger.warning(f"{self.LOG_TAG}读取通知配置失败：{err}")

        return [
            {
                "component": "VForm",
                "content": [
                    # 第一行：总开关
                    {
                        "component": "VRow",
                        "content": [
                            self._col(12, 3, self._switch("enabled", "启用插件",
                                "开启后按周期自动检查磁盘空间并清理")),
                            self._col(12, 3, self._switch("dry_run", "演练模式（只记录不删除）",
                                "建议先演练观察日志，确认候选符合预期后再关闭")),
                            self._col(12, 3, self._switch("onlyonce", "立即运行一次",
                                "保存配置后 3 秒执行一轮，执行后自动关闭")),
                            self._col(12, 3, self._switch("notify", "开启通知",
                                "每轮清理结果推送给配置的渠道")),
                        ],
                    },
                    # 第二行：触发条件
                    {
                        "component": "VRow",
                        "content": [
                            self._col(12, 4, {
                                "component": "VCronField",
                                "props": {"model": "cron", "label": "检查周期",
                                          "placeholder": "0 */6 * * *",
                                          "hint": "cron 表达式，默认每 6 小时", "persistent-hint": True},
                            }),
                            self._col(12, 4, {
                                "component": "VTextField",
                                "props": {"model": "monitor_path", "label": "监控盘目录",
                                          "placeholder": "E:/",
                                          "hint": "读取该路径所在磁盘的可用空间", "persistent-hint": True},
                            }),
                            self._col(12, 4, {
                                "component": "VTextField",
                                "props": {"model": "threshold_free_gb", "label": "触发阈值（可用空间 GiB）",
                                          "type": "number", "min": "0", "placeholder": "300",
                                          "hint": "低于该值开始清理", "persistent-hint": True},
                            }),
                        ],
                    },
                    # 第三行：目标与范围
                    {
                        "component": "VRow",
                        "content": [
                            self._col(12, 4, {
                                "component": "VTextField",
                                "props": {"model": "target_free_gb", "label": "停止线（清理到该可用空间 GiB）",
                                          "type": "number", "min": "0", "placeholder": "350",
                                          "hint": "删到回线即停，为软目标", "persistent-hint": True},
                            }),
                            self._col(12, 4, {
                                "component": "VSelect",
                                "props": {"model": "downloaders", "label": "下载器（多选，空=全部）",
                                          "items": downloader_items, "multiple": True, "chips": True,
                                          "clearable": True,
                                          "hint": "自动读取 MoviePilot 已配置的下载器，无需填地址密码",
                                          "persistent-hint": True},
                            }),
                            self._col(12, 4, {
                                "component": "VSelect",
                                "props": {"model": "notify_channels", "label": "通知渠道（多选，空=系统默认）",
                                          "items": notify_items, "multiple": True, "chips": True,
                                          "clearable": True,
                                          "hint": "自动读取 MoviePilot 已启用的通知渠道",
                                          "persistent-hint": True},
                            }),
                        ],
                    },
                    # 第四行：过滤条件
                    {
                        "component": "VRow",
                        "content": [
                            self._col(12, 4, {
                                "component": "VTextField",
                                "props": {"model": "min_seeding_days", "label": "最小做种天数",
                                          "type": "number", "min": "0", "placeholder": "15",
                                          "hint": "做种不满该天数不删", "persistent-hint": True},
                            }),
                            self._col(12, 4, {
                                "component": "VTextField",
                                "props": {"model": "protect_recent_days", "label": "完成保护天数",
                                          "type": "number", "min": "0", "placeholder": "7",
                                          "hint": "下载完成不满该天数不删，保护不稳定窗口", "persistent-hint": True},
                            }),
                            self._col(12, 4, {
                                "component": "VTextField",
                                "props": {"model": "max_delete_per_run", "label": "单轮最多删除任务数",
                                          "type": "number", "min": "1", "placeholder": "30",
                                          "hint": "单轮上限，防一次删太多", "persistent-hint": True},
                            }),
                        ],
                    },
                    # 第五行：更多过滤
                    {
                        "component": "VRow",
                        "content": [
                            self._col(12, 4, {
                                "component": "VTextField",
                                "props": {"model": "min_ratio", "label": "分享率门槛（0=关闭）",
                                          "type": "number", "min": "0", "placeholder": "0",
                                          "hint": "分享率低于该值不删，防 H&R 追责", "persistent-hint": True},
                            }),
                            self._col(12, 4, {
                                "component": "VTextField",
                                "props": {"model": "min_size_gb", "label": "小文件保护（GB，0=关闭）",
                                          "type": "number", "min": "0", "placeholder": "0",
                                          "hint": "小于该大小的任务不删", "persistent-hint": True},
                            }),
                            self._col(12, 4, {
                                "component": "VTextField",
                                "props": {"model": "delete_tag", "label": "两阶段删除标签名",
                                          "placeholder": "待清理",
                                          "hint": "两阶段模式下先打该标签，下轮才删", "persistent-hint": True},
                            }),
                        ],
                    },
                    # 第六行：目录与标签
                    {
                        "component": "VRow",
                        "content": [
                            self._col(12, 6, {
                                "component": "VTextarea",
                                "props": {"model": "clean_dirs",
                                          "label": "清理目录（每行一个，仅这些目录子树内的任务才会被清理）",
                                          "rows": 3, "placeholder": "E:/Downloads",
                                          "hint": "SSD / 保种目录不要加进来", "persistent-hint": True},
                            }),
                            self._col(12, 6, {
                                "component": "VTextarea",
                                "props": {"model": "protect_tags",
                                          "label": "保护标签（命中任一标签的任务不删除，每行一个）",
                                          "rows": 3, "placeholder": "保种\nH&R",
                                          "persistent-hint": True},
                            }),
                        ],
                    },
                    # 第七行：黑名单与白名单
                    {
                        "component": "VRow",
                        "content": [
                            self._col(12, 6, {
                                "component": "VTextarea",
                                "props": {"model": "block_keywords",
                                          "label": "路径/名称黑名单关键词（每行一个，命中即排除）",
                                          "rows": 2, "placeholder": "migration",
                                          "persistent-hint": True},
                            }),
                            self._col(12, 6, {
                                "component": "VTextarea",
                                "props": {"model": "verified_extra",
                                          "label": "额外入库白名单种子 Hash（每行一个）",
                                          "rows": 2,
                                          "hint": "经核查已入库但整理记录缺失的种子 Hash，一般留空",
                                          "persistent-hint": True},
                            }),
                        ],
                    },
                    # 第八行：动作开关
                    {
                        "component": "VRow",
                        "content": [
                            self._col(12, 4, self._switch("delete_files", "同时删除本地文件",
                                "关闭则只删任务、保留文件（不回收空间）")),
                            self._col(12, 4, self._switch("delete_history", "同步清理 MP 历史记录",
                                "删除任务同时清理整理记录与下载记录")),
                            self._col(12, 4, self._switch("require_library", "要求媒体已入库",
                                "整理记录成功或额外白名单命中才删（强烈建议保持开启）")),
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            self._col(12, 4, self._switch("clean_empty_dirs", "回收空目录",
                                "删除后清理清理目录子树下的空目录")),
                            self._col(12, 4, self._switch("tag_then_delete", "两阶段删除",
                                "第一轮只打标签，下一轮才真删，留出人工干预窗口")),
                            self._col(12, 4, {
                                "component": "VAlert",
                                "props": {"type": "error", "variant": "tonal",
                                          "text": "警告：qBittorrent 删除任务并删文件为永久删除、不进回收站，请谨慎开启"},
                            }),
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "dry_run": True,
            "onlyonce": False,
            "notify": True,
            "notify_channels": [],
            "cron": "0 */6 * * *",
            "monitor_path": "E:/",
            "threshold_free_gb": 300,
            "target_free_gb": 350,
            "downloaders": [],
            "clean_dirs": "E:/Downloads",
            "block_keywords": "",
            "min_seeding_days": 15,
            "protect_tags": "保种\nH&R",
            "protect_recent_days": 7,
            "min_ratio": 0,
            "min_size_gb": 0,
            "require_library": True,
            "verified_extra": "",
            "delete_files": True,
            "delete_history": True,
            "clean_empty_dirs": False,
            "tag_then_delete": False,
            "delete_tag": "待清理",
            "max_delete_per_run": 30,
        }

    # ---------------------------------------------------------------- 详情页

    def get_page(self) -> Optional[List[dict]]:
        statistic = self.get_data("statistic") or {}
        disk = self.get_data("disk") or {}
        candidates = self.get_data("candidates") or []
        history = self.get_data("history") or []

        # 顶部统计卡片
        cards = []
        for title, value, hint in (
            ("累计删除", f"{statistic.get('total_deleted') or 0} 个", "总量"),
            ("累计释放", f"{float(statistic.get('total_freed_gb') or 0):.1f} GiB", "总量"),
            ("本轮删除", f"{statistic.get('last_deleted') or 0} 个", statistic.get("last_mode") or "-"),
            ("当前可用", f"{disk.get('free_gb', 0):.1f} GiB", disk.get("checked_at") or "未检测"),
        ):
            cards.append(
                self._col(6, 3, {
                    "component": "VCard",
                    "props": {"variant": "tonal"},
                    "content": [
                        {"component": "VCardText", "props": {"class": "pa-2 text-h6"}, "text": str(value)},
                        {"component": "VCardText", "props": {"class": "pa-2 pt-0 text-caption"},
                         "text": f"{title} · {hint}"},
                    ],
                })
            )

        # 磁盘状态卡
        disk_card = self._col(12, 12, {
            "component": "VAlert",
            "props": {
                "type": "info", "variant": "tonal",
                "text": (f"监控盘 {disk.get('monitor', self._monitor_path)}："
                         f"可用 {disk.get('free_gb', 0):.1f} GiB，"
                         f"阈值 {disk.get('threshold', self._threshold_free_gb):.0f} GiB，"
                         f"目标线 {disk.get('target', self._target_free_gb):.0f} GiB，"
                         f"检测时间 {disk.get('checked_at', '-')}"),
            },
        })

        blocks: List[dict] = [
            {"component": "VRow", "content": cards},
            {"component": "VRow", "content": [disk_card]},
        ]

        # 当前候选表
        if candidates:
            blocks.append(self._table_card(
                "当前候选（若触发将按做种时长降序清理）",
                ["名称", "下载器", "大小(GiB)", "做种(天)", "分享率", "目录"],
                [[
                    {"component": "td", "text": c.get("name", "")[:60]},
                    {"component": "td", "text": c.get("downloader", "")},
                    {"component": "td", "text": f"{c.get('size', 0) / GIB:.1f}"},
                    {"component": "td", "text": f"{c.get('seed_days', 0):.1f}"},
                    {"component": "td", "text": f"{c.get('ratio', 0):.2f}"},
                    {"component": "td", "text": c.get("save_path", "")[:60]},
                ] for c in candidates[:30]]
            ))
        else:
            blocks.append(self._alert_card("当前候选", "暂无候选数据（空间未低于阈值或未到检测周期）", "info"))

        # 删除历史表
        if history:
            rows = []
            for h in reversed(history[-30:]):
                rows.append([
                    {"component": "td", "text": h.get("name", "")[:60]},
                    {"component": "td", "text": h.get("time", "")},
                    {"component": "td", "text": f"{h.get('size_gb', 0):.1f}"},
                    {"component": "td", "text": f"{h.get('seed_days', 0):.0f}"},
                    {"component": "td", "text": h.get("mode", "")},
                ])
            blocks.append(self._table_card("最近删除记录", ["名称", "时间", "大小(GiB)", "做种(天)", "模式"], rows))
        else:
            blocks.append(self._alert_card("最近删除记录", "暂无删除记录", "info"))

        return blocks

    # ---------------------------------------------------------------- 表单/页面组件助手

    @staticmethod
    def _col(cols: int, md: int, content: dict) -> dict:
        return {
            "component": "VCol",
            "props": {"cols": cols, "md": md},
            "content": [content],
        }

    @staticmethod
    def _switch(model: str, label: str, hint: str) -> dict:
        return {
            "component": "VSwitch",
            "props": {"model": model, "label": label, "hint": hint, "persistent-hint": True},
        }

    @staticmethod
    def _table_card(title: str, headers: List[str], rows: List[List[dict]]) -> dict:
        thead = [
            {"component": "th", "props": {"class": "text-start ps-4"}, "text": h}
            for h in headers
        ]
        return {
            "component": "VCard",
            "props": {"class": "mt-2"},
            "content": [
                {"component": "VCardTitle", "props": {"class": "text-subtitle-1"}, "text": title},
                {
                    "component": "VTable",
                    "props": {"hover": True, "density": "comfortable"},
                    "content": [
                        {"component": "thead", "props": {"class": "text-no-wrap"}, "content": thead},
                        {"component": "tbody", "content": [
                            {"component": "tr", "props": {"class": "text-sm"}, "content": row}
                            for row in rows
                        ]},
                    ],
                },
            ],
        }

    @staticmethod
    def _alert_card(title: str, text: str, alert_type: str) -> dict:
        return {
            "component": "VCard",
            "props": {"class": "mt-2"},
            "content": [
                {"component": "VCardTitle", "props": {"class": "text-subtitle-1"}, "text": title},
                {
                    "component": "VCardText",
                    "content": [
                        {"component": "VAlert", "props": {"type": alert_type, "variant": "tonal",
                                                          "text": text}},
                    ],
                },
            ],
        }
