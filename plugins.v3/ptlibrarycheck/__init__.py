"""PT 媒体库检测插件。

给 PT 站点的油猴脚本提供一个查询接口：传入种子标题（或站点已填的 IMDb ID），
返回该资源是否已在 MP 配置的媒体服务器（Emby / Plex / Jellyfin ...）中，以及
在库版本的分辨率 / 杜比视界 / HDR / 编码 / 音轨 / 字幕 / 路径。

设计要点：
- 插件复用 MP 已配置的媒体服务器凭据，**不在任何地方保存 Emby/Plex 密钥**；
- 识别优先用 imdb 参数（站点种子页已填的 IMDb ID 直接拿得到，最准也最快），
  没有 imdb 才退回 MetaInfo 工厂 + MediaChain 标题识别；
- Emby/Jellyfin 有 imdb 时用 AnyProviderIdEquals 直查条目，省掉按片名搜索；
  Plex 用 imdb 给候选加权（guid 里带 imdb://tt... 的直接命中）；
- 画质信息通过媒体服务器模块自带的 get_data() 取原始 JSON 解析，
  MediaInfo / MediaServerItem 都不带分辨率与 DoVi 字段，只能走原始数据。
"""

import inspect
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

# 官方插件开发指南（V3）：插件应优先从 app.sdk 稳定 SDK 导入，
# 不要直接依赖 app.application / app.domain 的内部文件位置。
# 这里保留旧路径兜底，兼容早期 V3 宿主。
try:
    from app.sdk.media import MetaInfo
except ImportError:  # 旧宿主没有 sdk.media 门面时回退
    from app.domain.metainfo import MetaInfo

try:
    from app.sdk.services import MediaServerHelper
except ImportError:  # 旧宿主回退
    from app.application.mediaserver import MediaServerHelper

from app.chain.media import MediaChain
from app.schemas.types import MediaType
from app.sdk.plugin import _PluginBase

try:  # MediaSource 是 MP v3 才有的枚举，缺失时自动降级为标题识别
    from app.schemas.types import MediaSource
except Exception:  # noqa: BLE001
    MediaSource = None  # type: ignore[assignment]

# 查询结果缓存 key 前缀
_CACHE_PREFIX = "ptmc_chk_"
# 单次查询最多返回的在库季集数量（避免响应体过大）
_MAX_SEASONS = 20
# IMDb ID：tt + 6~10 位数字
_IMDB_RE = re.compile(r"(tt\d{6,10})", re.I)

# 副标题里常见的发布标签词，抽中文片名时先剔掉它们
_CN_TAG_WORDS = (
    "中文字幕", "国语中字", "国语", "粤语", "英语", "日语", "韩语", "国配", "双语",
    "多语", "中字", "简繁", "简体", "繁体", "简中", "繁中", "字幕", "特效",
    "原盘", "DIY", "Remux", "重制", "修复", "加长版", "导演剪辑", "完整版",
    "未删减", "免费", "优惠", "剩余时间", "剩余", "限时", "置顶", "候选",
    "高清", "蓝光", "超清", "完结", "全集", "更新",
)


def _normalize_imdb(value: Any) -> str:
    """把站点上各种写法（完整 URL / 带空格 / 纯 tt1234567）统一成 tt1234567。"""
    if not value:
        return ""
    m = _IMDB_RE.search(str(value))
    return m.group(1).lower() if m else ""


class PtLibraryCheck(_PluginBase):
    """PT 站点媒体库存在性与画质查询。"""

    plugin_name = "PT媒体库检测"
    plugin_desc = "为 PT 站点油猴脚本提供媒体库存在性与画质查询，复用 MP 已配置的媒体服务器，" \
                  "无需在脚本里保存 Emby/Plex 密钥；优先用站点已填的 IMDb ID 识别。"
    plugin_icon = "icons/ptlibrarycheck.png"
    plugin_version = "1.1.7"
    plugin_author = "yxb2018"
    author_url = "https://github.com/yxb2018"
    plugin_label = "媒体库"
    plugin_order = 100
    plugin_config_prefix = "ptlibrarycheck_"
    # 1 = 普通权限；2 需要站点认证等级，未装配站点能力时会被 runtime 策略拦住
    auth_level = 1

    _enabled = False
    _allow_anonymous = False
    _cache_hours = 24
    _episode_check = True
    # 时间预算（秒）：油猴脚本走反代，反代对单个请求有 30s 硬上限，
    # 超了就是 504（前端表现为 network error）。所以必须自己先收手。
    _total_timeout = 20
    _recognize_timeout = 6

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def init_plugin(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.stop_service()
        self._enabled = False
        self._allow_anonymous = False
        self._cache_hours = 24
        self._episode_check = True
        self._total_timeout = 20
        self._recognize_timeout = 6
        if not config:
            return
        self._enabled = bool(config.get("enabled"))
        self._allow_anonymous = bool(config.get("allow_anonymous"))
        try:
            self._cache_hours = max(0, int(config.get("cache_hours") or 24))
        except (TypeError, ValueError):
            self._cache_hours = 24
        self._episode_check = bool(config.get("episode_check", True))
        for attr, key, default in (
            ("_total_timeout", "total_timeout", 20),
            ("_recognize_timeout", "recognize_timeout", 6),
        ):
            try:
                setattr(self, attr, max(1, int(config.get(key) or default)))
            except (TypeError, ValueError):
                setattr(self, attr, default)

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """不注册远程命令（官方 V3 骨架要求的显式空实现）。"""
        return []

    def get_page(self):
        """无独立详情页，配置表单已够用。"""
        return None

    def stop_service(self) -> None:
        """无后台服务需要停止。"""
        return None

    # ------------------------------------------------------------------
    # 配置表单（MP 插件页直接渲染，无需前端构建）
    # ------------------------------------------------------------------
    def get_form(self):
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "enabled",
                            "label": "启用插件",
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "allow_anonymous",
                            "label": "允许匿名访问查询接口（关闭则油猴需填 MP API Key）",
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "episode_check",
                            "label": "查询剧集时返回具体分集是否在库",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "cache_hours",
                            "label": "查询缓存小时数（0 = 不缓存）",
                            "type": "number",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "total_timeout",
                            "label": "单次查询总超时（秒，建议 20；反代通常 30s 断流）",
                            "type": "number",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "recognize_timeout",
                            "label": "标题识别超时（秒，建议 6；超时自动跳过识别按片名搜）",
                            "type": "number",
                        },
                    },
                ],
            }
        ], {
            "enabled": False,
            "allow_anonymous": False,
            "episode_check": True,
            "cache_hours": 24,
            "total_timeout": 20,
            "recognize_timeout": 6,
        }

    # ------------------------------------------------------------------
    # HTTP 接口
    # ------------------------------------------------------------------
    def get_api(self) -> List[Dict[str, Any]]:
        """注册插件 API。

        路径最终挂载为 /api/v1/plugin/PtLibraryCheck/check。
        allow_anonymous 为 True 时宿主不加任何鉴权依赖，油猴可零密钥调用。
        """
        return [
            {
                "path": "/check",
                "endpoint": self.check,
                "methods": ["GET", "POST"],
                "auth": "apikey",
                "allow_anonymous": self._allow_anonymous,
                "summary": "查询媒体库是否存在该资源",
                "description": "传入种子标题（可带副标题）或 imdb=tt1234567，"
                               "返回存在性、在库版本画质与分集情况",
            }
        ]

    async def check(self,
                    title: str = "",
                    subtitle: str = "",
                    season: Optional[int] = None,
                    episode: Optional[int] = None,
                    imdb: str = "",
                    debug: int = 0) -> Dict[str, Any]:
        """查询入口。阻塞的媒体服务器调用放到线程池，避免卡住事件循环。

        imdb 优先：站点种子页已填 IMDb 时传进来，识别更快也更准；
        没有才用 title + subtitle 解析。

        debug=1 时在返回体里附带解析结果、各阶段耗时与匹配置信度，便于排查
        「为什么这个是未入库 / 为什么这么慢」，正常调用不带这个字段。
        """
        if not self._enabled:
            return {"success": False, "message": "插件未启用"}
        title = (title or "").strip()
        imdb = _normalize_imdb(imdb)
        if not title and not imdb:
            return {"success": False, "message": "缺少 title 或 imdb 参数"}
        import asyncio
        try:
            data = await asyncio.to_thread(
                self._query, title, (subtitle or "").strip(), season, episode,
                imdb, bool(debug),
            )
        except Exception as err:  # noqa: BLE001 - 接口不能把异常抛给前端
            return {"success": False, "message": f"查询失败：{err}"}
        return {"success": True, "data": data}

    # ------------------------------------------------------------------
    # 查询主流程
    # ------------------------------------------------------------------
    def _query(self, title: str, subtitle: str,
               want_season: Optional[int], want_episode: Optional[int],
               imdb: str = "", debug: bool = False) -> Dict[str, Any]:
        # 缓存（debug 请求一定要看真实耗时，不读缓存）
        cache_key = None
        if self._cache_hours > 0 and not debug:
            raw = f"{imdb}|{title}\n{subtitle}|{want_season}|{want_episode}"
            cache_key = _CACHE_PREFIX + str(abs(hash(raw)))
            hit = self.get_data(cache_key)
            if isinstance(hit, dict) and hit.get("_t"):
                if time.time() - hit["_t"] < self._cache_hours * 3600:
                    return hit["v"]

        result = self._query_uncached(title, subtitle, want_season, want_episode,
                                      imdb, debug)

        # 超时的结果不写缓存，下次还会重试
        if cache_key and not result.get("timeout"):
            try:
                self.save_data(cache_key, {"_t": time.time(), "v": result})
            except Exception:  # noqa: BLE001 - 缓存失败不影响查询
                pass
        return result

    def _query_uncached(self, title: str, subtitle: str,
                        want_season: Optional[int], want_episode: Optional[int],
                        imdb: str = "", debug: bool = False) -> Dict[str, Any]:
        """查询主流程。

        设计原则（性能即功能）：反代 30s 断流，超时就是「没有状态」，
        所以整个查询必须有时间预算，且**默认路径尽量不碰 TMDb**：

          第一轮  只用本地 MetaInfo 解析出的片名去媒体服务器搜，
                  命中「片名完全相同 / imdb 命中」就直接返回，零识别开销；
          第二轮  只有第一轮没找准才做标题识别（硬超时，默认 6s），
                  拿到 tmdb_id 与额外片名后重新评分；识别超时也照样返回结论。
        """
        t_start = time.time()
        budget_total = self._budget("total_timeout", 20)
        deadline = t_start + budget_total
        dbg: Optional[Dict[str, Any]] = None
        if debug:
            dbg = {
                "budget": {"total": budget_total,
                           "recognize": self._budget("recognize_timeout", 6)},
                "servers": [],
            }

        # 1. 标题解析（MP 原生 MetaInfo 工厂，返回 MetaVideo/MetaAnime/MetaMusic）
        #    即便有 imdb 也要解析：季集号写在标题里，imdb 不带季集信息
        #    只给 imdb 不给标题时跳过解析
        meta = MetaInfo(title, subtitle or None) if title else None
        is_tv = bool(meta) and (meta.type == MediaType.TV or bool(meta.begin_season))
        if want_season is not None:
            season = want_season
        else:
            season = getattr(meta, "begin_season", None)
        if want_episode is not None:
            episode = want_episode
        else:
            episode = getattr(meta, "begin_episode", None)

        names = self._candidate_names(meta) if meta is not None else []
        year = getattr(meta, "year", None)
        # 副标题里的中文名：PT 站常见「主标题=英文原名，中文片名只在副标题里」，
        # 而媒体库里的条目往往以中文命名 —— 只用英文名搜会 0 候选。
        # 注意：它只进搜索候选，**不参与 tooltip 的「识别为」**（那必须如实反映 MP 的解析结果）
        sub_names = self._subtitle_names(subtitle)
        if sub_names:
            names = [n for n in sub_names if n not in names] + names
        # MP 自己解析出来的片名（诊断用：徽标 tooltip 显示它，绝不拿发布名充数）
        label, label_src = self._display_label(meta)
        state: Dict[str, Any] = {"label": label, "src": label_src, "recognized": None}
        if dbg is not None:
            dbg["parse"] = {
                "cost": round(time.time() - t_start, 2),
                "is_tv": is_tv,
                "season": season,
                "episode": episode,
                "year": year,
                "names": names,
                "sub_names": sub_names,
                "display": label,
                "name": getattr(meta, "name", None),
                "cn_name": getattr(meta, "cn_name", None),
                "en_name": getattr(meta, "en_name", None),
            }

        def _done(payload: Dict[str, Any]) -> Dict[str, Any]:
            """统一出口：补上 MP 的解析结果（徽标 tooltip 用），不用发布名。"""
            payload.setdefault("display", state["label"])
            payload.setdefault("display_src", state["src"])
            if state["recognized"]:
                payload.setdefault("recognized", state["recognized"])
            if dbg is not None:
                dbg["parse"]["display"] = state["label"]
            return self._finish(payload, dbg, t_start)

        if not names and not imdb:
            return _done({"found": False, "reason": "无法解析片名", "title": title})

        # 2. 遍历 MP 里配置的所有媒体服务器，先只搜不判断
        helper = MediaServerHelper()
        services = helper.get_services()
        if not services:
            return _done({"found": False, "reason": "MP 未配置媒体服务器", "title": title})

        errors: List[str] = []
        collected: List[Tuple[str, Any, str, List[dict]]] = []
        for name, svc in services.items():
            if time.time() > deadline:
                errors.append(f"{name}: 超出时间预算，跳过")
                continue
            server = svc.instance
            # MediaServerConf.type 当前是 str；兼容未来改成 Enum 的情况
            raw_type = svc.type
            stype = str(getattr(raw_type, "value", raw_type) or "").lower()
            if not server or stype not in ("emby", "jellyfin", "plex"):
                continue
            t_srv = time.time()
            try:
                if stype == "plex":
                    items = self._search_plex(server, names, is_tv, imdb)
                else:
                    items = self._search_emby(server, names, is_tv, imdb)
            except Exception as err:  # noqa: BLE001 - 单个服务器失败不影响其他
                errors.append(f"{name}: {err}")
                continue
            cost_srv = time.time() - t_srv
            collected.append((name, server, stype, items))

            # 第一轮判定：只认「片名完全相同（100/95）」或「imdb 命中（400）」
            best = self._pick_best_scored(items, names, year, None,
                                         emby=(stype != "plex"), imdb=imdb)
            if dbg is not None:
                dbg["servers"].append({
                    "name": name, "type": stype, "cost": round(cost_srv, 2),
                    "candidates": len(items),
                    "best": (best[0].get("Name") or best[0].get("title")) if best else None,
                    "score": best[1] if best else 0,
                    "year_conflict": best[2] if best else None,
                })
            if best and best[1] >= 95:
                hit = self._hit_result(name, stype, server, best[0], is_tv,
                                       season, episode, imdb, None)
                if hit:
                    return _done(hit)
            # 候选为空时不必再等其它服务器 —— 但其它服务器可能有，所以继续

        # 3. 第一轮没找准：做一次标题识别（硬超时）补 tmdb_id 与额外片名
        tmdb_id = None
        matched_by = None
        extra_names: List[str] = []
        if time.time() < deadline:
            remain = deadline - time.time()
            info, timed_out, cost = self._recognize_bounded(
                meta, is_tv, imdb,
                timeout=min(float(self._budget("recognize_timeout", 6)), remain),
            )
            if dbg is not None:
                dbg["recognize"] = {
                    "cost": round(cost, 2), "timed_out": timed_out, "ok": info is not None,
                }
            if info is not None:
                matched_by = "imdb" if imdb else "title"
                if getattr(info, "tmdb_id", None):
                    tmdb_id = str(info.tmdb_id)
                recog: Dict[str, Any] = {}
                for key, attr in (("title", "title"), ("original_title", "original_title")):
                    value = str(getattr(info, attr, None) or "").strip()
                    if value and not _IMDB_RE.search(value):
                        recog[key] = value
                if tmdb_id:
                    recog["tmdb_id"] = tmdb_id
                if recog:
                    state["recognized"] = recog
                    # MP 连片名都解析不出来时，用识别结果兜底显示（source 标记为 recognize）
                    if not state["label"]:
                        state["label"] = recog.get("title") or recog.get("original_title") or ""
                        state["src"] = "recognize"
                for value in (getattr(info, "title", None),
                              getattr(info, "original_title", None)):
                    value = str(value or "").strip()
                    # 有些版本识别结果里 title 直接是 imdb id，别拿它当搜索词
                    if not value or _IMDB_RE.search(value):
                        continue
                    if self._informative(value) and value not in names:
                        extra_names.append(value)

        # 3.1 识别给出了新的片名：补搜一次（只有这时才多打几个请求）
        if extra_names:
            if dbg is not None:
                dbg["extra_names"] = extra_names
            names = names + extra_names
            for idx, (name, server, stype, items) in enumerate(list(collected)):
                if time.time() > deadline:
                    break
                try:
                    if stype == "plex":
                        more = self._search_plex(server, extra_names, is_tv, imdb)
                    else:
                        more = self._search_emby(server, extra_names, is_tv, imdb)
                except Exception as err:  # noqa: BLE001
                    errors.append(f"{name}: {err}")
                    continue
                merged, seen = [], set()
                for it in items + more:
                    key = it.get("Id") or it.get("ratingKey") or id(it)
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(it)
                collected[idx] = (name, server, stype, merged)

        # 4. 第二轮判定：带上 tmdb_id / 全部片名重新评分
        suspect: Tuple[Any, int, bool] = (None, 0, False)
        for idx, (name, server, stype, items) in enumerate(collected):
            best = self._pick_best_scored(items, names, year, tmdb_id,
                                         emby=(stype != "plex"), imdb=imdb)
            if dbg is not None and idx < len(dbg["servers"]):
                dbg["servers"][idx]["best2"] = (
                    best[0].get("Name") or best[0].get("title")
                    or best[0].get("originalTitle")) if best else None
                dbg["servers"][idx]["score2"] = best[1] if best else 0
                dbg["servers"][idx]["year_conflict2"] = best[2] if best else None
            if not best:
                continue
            if not self._accept_score(best[1], best[2]):
                # 分数不够：不当结论，但留作「疑似」提示（同名不同片最常见）
                if best[1] > suspect[1]:
                    suspect = best
                continue
            hit = self._hit_result(name, stype, server, best[0], is_tv,
                                   season, episode, imdb, matched_by)
            if hit:
                return _done(hit)

        if time.time() > deadline:
            out = {"found": False, "timeout": True, "retryable": True,
                   "reason": f"查询超时（{budget_total}s 预算用尽）", "title": title}
        else:
            reason = "媒体库无此资源"
            if not names:
                # 只给 imdb 没给标题：Emby/Jellyfin 能直查，Plex 只能按片名搜
                reason += "（只传 imdb 时 Plex 无法定位，请同时传 title）"
            if errors:
                reason += "（" + "；".join(errors[:2]) + "）"
            if suspect[0] is not None:
                s_item, s_score, s_conf = suspect
                s_name = (s_item.get("Name") or s_item.get("title")
                          or s_item.get("originalTitle") or "")
                s_year = s_item.get("ProductionYear") or s_item.get("year") or ""
                how = "片名相同但年份不符" if s_conf else "片名相近"
                reason = (f"{how}（库内：{s_name}{(' ' + str(s_year)) if s_year else ''}"
                          f"，评分 {s_score}），{reason}")
                if dbg is not None:
                    dbg["suspect"] = {"name": s_name, "year": s_year,
                                      "score": s_score, "year_conflict": s_conf}
            out = {"found": False, "reason": reason, "title": title}
        if imdb:
            out["imdb"] = imdb
        return _done(out)

    @staticmethod
    def _finish(out: Dict[str, Any], dbg: Optional[Dict[str, Any]],
                t_start: float) -> Dict[str, Any]:
        if dbg is not None:
            dbg["elapsed"] = round(time.time() - t_start, 2)
            out["debug"] = dbg
        return out

    def _hit_result(self, name: str, stype: str, server: Any, best: dict,
                    is_tv: bool, season: Optional[int], episode: Optional[int],
                    imdb: str, matched_by: Optional[str]) -> Optional[Dict[str, Any]]:
        if stype == "plex":
            hit = self._finish_plex(server, best, is_tv, season, episode)
        else:
            hit = self._finish_emby(server, best, is_tv, season, episode)
        if not hit:
            return None
        hit["server"] = name
        hit["server_type"] = stype
        if imdb:
            hit["imdb"] = imdb
        # 命中依据：条目自身带的 imdb 与请求一致 → imdb；否则就是片名
        if imdb and self._item_imdb(best, stype != "plex") == imdb:
            hit["matched_by"] = "imdb"
        elif matched_by:
            hit["matched_by"] = matched_by
        else:
            hit["matched_by"] = "title"
        return hit

    def _budget(self, key: str, default: int) -> int:
        """读时间预算配置，非法值回退默认。"""
        try:
            return max(1, int(getattr(self, "_" + key, default) or default))
        except (TypeError, ValueError):
            return default

    def _recognize_bounded(self, meta: Any, is_tv: bool, imdb: str,
                           timeout: float) -> Tuple[Any, bool, float]:
        """带硬超时的识别。

        识别（TMDb/TVDB）遇到综艺/直播/体育这类片名会一路重试到几十秒，
        直接把整个请求拖过反代 30s 上限。放到后台线程里跑，到点就走人：
        超时后线程自行结束（daemon），主流程照常用片名搜索兜底。
        """
        box: Dict[str, Any] = {}
        t0 = time.time()

        def _run() -> None:
            try:
                box["r"] = self._recognize(meta, is_tv, imdb)
            except Exception as err:  # noqa: BLE001 - 识别失败不是致命错误
                box["e"] = err

        th = threading.Thread(target=_run, name="ptmc-recognize", daemon=True)
        th.start()
        th.join(max(0.5, float(timeout)))
        timed_out = th.is_alive()
        if timed_out:
            self._log(f"标题识别超过 {timeout:.0f}s，跳过（按片名搜索兜底）")
        return box.get("r"), timed_out, time.time() - t0

    # ------------------------------------------------------------------
    # 识别
    # ------------------------------------------------------------------
    def _recognize(self, meta: Any, is_tv: bool, imdb: str) -> Any:
        """优先用 IMDb 直查媒体信息，失败再退回标题识别。

        MP 各版本识别签名差异很大（同一个函数名，参数名从 meta 改成 metainfo、
        又新增了 media_source/media_id 身份识别），硬编码任何一种都会在别的版本上
        TypeError。所以这里先读函数签名，只提交对方真正接受的参数，逐个候选尝试：

          · 身份识别   recognize_media(media_source=MediaSource.IMDb, media_id="tt…")
          · 标题识别   recognize_by_meta(metainfo=…) / (meta=…)
        全都拿不到也不阻断查询 —— 后面还有按片名搜索兜底。
        """
        chain = MediaChain()
        mtype = MediaType.TV if is_tv else MediaType.MOVIE

        if imdb:
            for fn_name in ("recognize_media", "recognize_by_meta"):
                fn = getattr(chain, fn_name, None)
                if not fn:
                    continue
                for source in ((MediaSource.IMDb, "imdb") if MediaSource is not None else ()):
                    info = self._call_adaptive(fn, (
                        {"media_source": source, "media_id": imdb, "mtype": mtype},
                        {"media_source": source, "media_id": imdb},
                    ))
                    if info:
                        return info

        if meta is None:
            return None
        for fn_name in ("recognize_by_meta", "recognize_media"):
            fn = getattr(chain, fn_name, None)
            if not fn:
                continue
            info = self._call_adaptive(fn, (
                {"metainfo": meta, "mtype": mtype},
                {"meta": meta, "mtype": mtype},
                {"metainfo": meta},
                {"meta": meta},
            ))
            if info:
                return info
        return None

    def _call_adaptive(self, fn: Any, candidates: Tuple[Dict[str, Any], ...]) -> Any:
        """按函数签名挑一组对方真能接受的参数调用；全都不匹配/失败则返回 None。

        跨 MP 版本的关键：签名里没有的关键字坚决不传（否则 TypeError 被上层吞掉，
        表现成「识别不到」，极难排查）。
        """
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            params = {}
        accepts_any = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        for kwargs in candidates:
            if not accepts_any and not all(k in params for k in kwargs):
                continue
            try:
                info = fn(**kwargs)
            except Exception as err:  # noqa: BLE001 - 单个候选失败就换下一个
                self._log(f"{getattr(fn, '__name__', 'recognize')} 调用失败：{err}")
                continue
            if info:
                return info
        return None

    # ------------------------------------------------------------------
    # Emby / Jellyfin
    # ------------------------------------------------------------------
    def _check_emby(self, server: Any, names: List[str], year: Optional[str],
                    tmdb_id: Optional[str], is_tv: bool,
                    season: Optional[int], episode: Optional[int],
                    imdb: str = "") -> Optional[Dict[str, Any]]:
        """搜索结果 + 选优 + 组装（保留给按服务器整体查询的场景）。"""
        items = self._search_emby(server, names, is_tv, imdb)
        best = self._pick_best(items, names, year, tmdb_id, emby=True, imdb=imdb)
        if not best:
            return None
        return self._finish_emby(server, best, is_tv, season, episode)

    def _search_emby(self, server: Any, names: List[str], is_tv: bool,
                     imdb: str = "") -> List[dict]:
        """只做搜索，把候选条目全部返回（选优交给 _pick_best_scored）。"""
        item_type = "Series" if is_tv else "Movie"
        fields = ("Fields=MediaSources,MediaStreams,ProviderIds,ProductionYear,"
                  "OriginalTitle,Path")
        items: List[dict] = []
        seen = set()

        # IMDb 直查：Emby / Jellyfin 的 AnyProviderIdEquals=imdb.tt1234567
        # 一次就能定位条目，省掉按片名搜索 + 名称模糊比对
        if imdb:
            url = (
                f"[HOST]emby/Items?Recursive=true&IncludeItemTypes={item_type}"
                f"&AnyProviderIdEquals=imdb.{imdb}&Limit=5&{fields}&api_key=[APIKEY]"
            )
            try:
                res = server.get_data(url)
            except Exception as err:  # noqa: BLE001 - 直查失败退回名称搜索
                self._log(f"Emby IMDb 直查失败：{err}")
                res = None
            if res:
                for it in (res.json() or {}).get("Items") or []:
                    if it.get("Id") and it["Id"] not in seen:
                        seen.add(it["Id"])
                        items.append(it)

        for term in (names[:3] if not items else []):
            url = (
                f"[HOST]emby/Items?Recursive=true&IncludeItemTypes={item_type}"
                f"&searchTerm={quote(term)}&Limit=20"
                f"&{fields}&api_key=[APIKEY]"
            )
            res = server.get_data(url)
            if not res:
                continue
            for it in (res.json() or {}).get("Items") or []:
                if it.get("Id") and it["Id"] not in seen:
                    seen.add(it["Id"])
                    items.append(it)
            if items:
                break

        if not items and is_tv:
            # 剧集名里带季号可能搜不到，退回裸名再搜一次
            for term in names[:2]:
                bare = re.sub(r"[第S]\s*\d+[季部]?", "", term).strip()
                if not bare or bare == term:
                    continue
                url = (
                    f"[HOST]emby/Items?Recursive=true&IncludeItemTypes=Series"
                    f"&searchTerm={quote(bare)}&Limit=20&{fields}&api_key=[APIKEY]"
                )
                res = server.get_data(url)
                if not res:
                    continue
                for it in (res.json() or {}).get("Items") or []:
                    if it.get("Id") and it["Id"] not in seen:
                        seen.add(it["Id"])
                        items.append(it)
                if items:
                    break
        return items

    def _finish_emby(self, server: Any, best: dict, is_tv: bool,
                     season: Optional[int], episode: Optional[int]) -> Optional[Dict[str, Any]]:
        out: Dict[str, Any] = {
            "found": True,
            "type": "tv" if is_tv else "movie",
            "title": best.get("Name") or "",
            "year": best.get("ProductionYear"),
            "item_id": best.get("Id"),
            "quality": self._emby_quality(best),
        }

        if not is_tv:
            return out

        # 剧集：列出在库季集
        seasons = self._emby_seasons(server, best.get("Id"))
        out["seasons"] = {str(k): v for k, v in list(seasons.items())[:_MAX_SEASONS]}
        if season is not None:
            out["episode_count"] = len(seasons.get(season) or [])
        if season is not None and episode is not None and self._episode_check:
            eps = seasons.get(season) or []
            if episode in eps:
                ep_item = self._emby_episode_item(server, best.get("Id"), season, episode)
                if ep_item:
                    out["quality"] = self._emby_quality(ep_item)
                    out["episode"] = {"season": season, "episode": episode, "found": True}
            else:
                out["episode"] = {"season": season, "episode": episode, "found": False}
                out["reason"] = f"剧集在库，但缺 S{season:02d}E{episode:02d}"
        elif season is not None:
            out["season_in_library"] = season in seasons
        return out

    def _emby_seasons(self, server: Any, series_id: Optional[str]) -> Dict[int, List[int]]:
        if not series_id:
            return {}
        url = (
            f"[HOST]emby/Items?ParentId={series_id}&IncludeItemTypes=Episode"
            f"&Recursive=true&Fields=Overview&api_key=[APIKEY]"
        )
        res = server.get_data(url)
        if not res:
            return {}
        seasons: Dict[int, List[int]] = {}
        for ep in (res.json() or {}).get("Items") or []:
            s, e = ep.get("ParentIndexNumber"), ep.get("IndexNumber")
            if s is None or e is None:
                continue
            seasons.setdefault(s, []).append(e)
        for s in seasons:
            seasons[s] = sorted(set(seasons[s]))
        return dict(sorted(seasons.items()))

    def _emby_episode_item(self, server: Any, series_id: Optional[str],
                           season: int, episode: int) -> Optional[dict]:
        if not series_id:
            return None
        url = (
            f"[HOST]emby/Items?ParentId={series_id}&IncludeItemTypes=Episode"
            f"&Recursive=true&Fields=MediaSources,MediaStreams&api_key=[APIKEY]"
        )
        res = server.get_data(url)
        if not res:
            return None
        for ep in (res.json() or {}).get("Items") or []:
            if ep.get("ParentIndexNumber") == season and ep.get("IndexNumber") == episode:
                if not (ep.get("MediaSources") or []):
                    # 少数情况下 Items 不返回 MediaSources，退回 PlaybackInfo
                    uid = getattr(server, "user", "") or ""
                    pb = server.get_data(
                        f"[HOST]emby/Items/{ep.get('Id')}/PlaybackInfo"
                        f"?UserId={uid}&api_key=[APIKEY]"
                    )
                    if pb:
                        return pb.json()
                return ep
        return None

    # ------------------------------------------------------------------
    # Plex
    # ------------------------------------------------------------------
    def _check_plex(self, server: Any, names: List[str], year: Optional[str],
                    tmdb_id: Optional[str], is_tv: bool,
                    season: Optional[int], episode: Optional[int],
                    imdb: str = "") -> Optional[Dict[str, Any]]:
        candidates = self._search_plex(server, names, is_tv)
        best = self._pick_best(candidates, names, year, tmdb_id, emby=False, imdb=imdb)
        if not best:
            return None
        return self._finish_plex(server, best, is_tv, season, episode)

    def _search_plex(self, server: Any, names: List[str], is_tv: bool,
                     imdb: str = "") -> List[dict]:
        """Plex 只能按片名搜，imdb 用于在 _pick_best_scored 里加权。"""
        mtype = 2 if is_tv else 1
        candidates: List[dict] = []
        seen = set()
        for term in names[:3]:
            for endpoint in (
                f"/library/all?type={mtype}&title={quote(term)}",
                f"/search?type={mtype}&query={quote(term)}",
            ):
                res = server.get_data(endpoint)
                if not res:
                    continue
                try:
                    payload = res.json()
                except Exception:  # noqa: BLE001
                    continue
                for md in ((payload or {}).get("MediaContainer") or {}).get("Metadata") or []:
                    rk = md.get("ratingKey")
                    if rk and rk not in seen:
                        seen.add(rk)
                        candidates.append(md)
            if candidates:
                break
        return candidates

    def _finish_plex(self, server: Any, best: dict, is_tv: bool,
                     season: Optional[int], episode: Optional[int]) -> Optional[Dict[str, Any]]:
        rk = best.get("ratingKey")
        out: Dict[str, Any] = {
            "found": True,
            "type": "tv" if is_tv else "movie",
            "title": best.get("title") or "",
            "year": best.get("year"),
            "item_id": rk,
        }
        detail = self._plex_metadata(server, rk)
        if detail:
            out["quality"] = self._plex_quality(detail)
        if not is_tv:
            return out

        # Plex 季集：/library/metadata/<id>/children?type=4 拿不到全集，
        # 直接用 grandchildren 逐季查询太重，这里按 season 查一季
        if season is not None and self._episode_check:
            eps = self._plex_season_episodes(server, rk, season)
            out["seasons"] = {str(season): eps[:_MAX_SEASONS]} if eps else {}
            out["episode_count"] = len(eps)
            if episode is not None:
                if episode in eps:
                    out["episode"] = {"season": season, "episode": episode, "found": True}
                else:
                    out["episode"] = {"season": season, "episode": episode, "found": False}
                    out["reason"] = f"剧集在库，但缺 S{season:02d}E{episode:02d}"
        return out

    def _plex_metadata(self, server: Any, rating_key: Optional[str]) -> Optional[dict]:
        if not rating_key:
            return None
        res = server.get_data(f"/library/metadata/{rating_key}")
        if not res:
            return None
        try:
            return res.json()
        except Exception:  # noqa: BLE001
            return None

    def _plex_season_episodes(self, server: Any, rating_key: Optional[str],
                              season: int) -> List[int]:
        if not rating_key:
            return []
        res = server.get_data(f"/library/metadata/{rating_key}/children?type=4")
        if not res:
            return []
        try:
            payload = res.json()
        except Exception:  # noqa: BLE001
            return []
        seasons = ((payload or {}).get("MediaContainer") or {}).get("Metadata") or []
        target = next((s for s in seasons if s.get("index") == season), None)
        if not target:
            return []
        eps_res = server.get_data(f"/library/metadata/{target.get('ratingKey')}/children")
        if not eps_res:
            return []
        try:
            eps_payload = eps_res.json()
        except Exception:  # noqa: BLE001
            return []
        eps = ((eps_payload or {}).get("MediaContainer") or {}).get("Metadata") or []
        return sorted({e.get("index") for e in eps if e.get("index") is not None})

    # ------------------------------------------------------------------
    # 画质解析
    # ------------------------------------------------------------------
    @staticmethod
    def _resolution_of(width: int, height: int) -> Optional[str]:
        if width >= 3800 or height >= 2100:
            return "2160p"
        if width >= 2500 or height >= 1400:
            return "1440p"
        if width >= 1800 or height >= 1000:
            return "1080p"
        if width >= 1200 or height >= 700:
            return "720p"
        if width > 0:
            return "SD"
        return None

    def _emby_quality(self, item: dict) -> Dict[str, Any]:
        src = (item.get("MediaSources") or [{}])[0]
        streams = src.get("MediaStreams") or []
        video = next((s for s in streams if s.get("Type") == "Video"), {})
        audio = [s.get("DisplayTitle") for s in streams
                 if s.get("Type") == "Audio" and s.get("DisplayTitle")]
        subs = [s.get("DisplayTitle") for s in streams
                if s.get("Type") == "Subtitle" and s.get("DisplayTitle")]
        width = video.get("Width") or 0
        height = video.get("Height") or 0
        range_type = (video.get("VideoRangeType") or "").upper()
        display = video.get("DisplayTitle") or ""
        dv = "DOVI" in range_type or "DOLBY VISION" in display.upper()
        if dv:
            hdr = "DOVI"
        else:
            hdr = video.get("VideoRange") or ""
            if not hdr:
                m = re.search(r"HDR10\+|HDR10|HDR|HLG", display, re.I)
                hdr = m.group(0) if m else "SDR"
        return {
            "src_name": src.get("Name") or item.get("Name") or "",
            "path": src.get("Path") or "",
            "resolution": self._resolution_of(width, height),
            "dv": dv,
            "hdr": hdr,
            "codec": video.get("Codec"),
            "audio": audio,
            "subtitles": subs,
        }

    def _plex_quality(self, container: dict) -> Dict[str, Any]:
        md = ((container or {}).get("MediaContainer") or {}).get("Metadata") or []
        if not md:
            return {}
        media = (md[0].get("Media") or [{}])[0]
        part = (media.get("Part") or [{}])[0]
        streams = part.get("Stream") or []
        video = next((s for s in streams if s.get("streamType") == 1), {})
        audio = [s.get("displayTitle") for s in streams
                 if s.get("streamType") == 2 and s.get("displayTitle")]
        subs = [s.get("displayTitle") for s in streams
                if s.get("streamType") == 3 and s.get("displayTitle")]
        title = video.get("extendedDisplayTitle") or video.get("displayTitle") or ""
        dv = bool(re.search(r"Dolby Vision|DoVi|\bDV\b", title))
        if dv:
            hdr = "DOVI"
        else:
            m = re.search(r"HDR10\+|HDR10|HDR|HLG", title, re.I)
            hdr = m.group(0) if m else "SDR"
        return {
            "src_name": media.get("title") or "",
            "path": part.get("file") or "",
            "resolution": self._resolution_of(
                int(video.get("width") or 0), int(video.get("height") or 0)),
            "dv": dv,
            "hdr": hdr,
            "codec": video.get("codec"),
            "audio": audio,
            "subtitles": subs,
        }

    # ------------------------------------------------------------------
    # 匹配
    # ------------------------------------------------------------------
    @staticmethod
    def _informative(name: str) -> bool:
        """这个名字能不能用来定位一条媒体。

        MP 的 MetaInfo 会把一些直播/体育标题解析成 "The"、"2026" 这种碎片，
        拿它去做包含匹配会命中一堆无关条目（实测把 CCTV5 开幕式判定成
        《The Adventurers》），所以短到没有信息量的名字一律不参与匹配。
        """
        core = re.sub(r"[\s\W_]+", "", (name or "").strip())
        if len(core) < 2:
            return False
        if core.isdigit():
            return False
        if re.fullmatch(r"[A-Za-z]{1,3}", core):
            return False
        return True

    @staticmethod
    def _containment_ok(c: str, w: str) -> bool:
        """包含匹配的额外门槛：短的那侧必须有信息量，且不能短得离谱。"""
        short, long_ = (c, w) if len(c) <= len(w) else (w, c)
        if not PtLibraryCheck._informative(short):
            return False
        if re.search(r"[\u3400-\u9fff]", short):
            # 中文片名本来就短（4 个字很常见），包含即算命中
            return True
        return len(short) / max(1, len(long_)) >= 0.5

    @staticmethod
    def _candidate_names(meta: Any) -> List[str]:
        """按优先级给出可用于搜索与比对的名称列表。"""
        names: List[str] = []
        for value in (
            getattr(meta, "cn_name", None),
            getattr(meta, "en_name", None),
            getattr(meta, "original_name", None),
            getattr(meta, "name", None),
            getattr(meta, "title", None),
        ):
            if not value:
                continue
            value = str(value).strip()
            if not value or value in names:
                continue
            # 去掉季集后缀，避免搜索词带季号
            value = re.sub(r"[第S]\s*\d+\s*[季部]?", "", value).strip()
            if len(value) < 2:
                continue
            if not PtLibraryCheck._informative(value):
                continue
            if value not in names:
                names.append(value)
        return names

    @classmethod
    def _subtitle_names(cls, subtitle: str) -> List[str]:
        """从副标题里抽取中文片名。

        PT 站点的发布名经常是「英文原名 + 副标题中文名」：主标题
        `A Chinese Ghost Story II 1990 UHD BluRay …`，中文名
        `倩女幽魂2：人间道` 只出现在同行的副标题里。MP 的 MetaInfo 在这种
        组合下会把中文副标题当噪声丢掉（cn_name 为空），于是按英文名去
        Plex 搜 —— 而库里条目叫「倩女幽魂2：人间道」，直接 0 候选。

        这里不依赖 MetaInfo：先砍掉方/圆括号里的发布说明，再取连续中文片段，
        剔除纯标签（国语/中字/简繁…）与纯季集号，剩下的当作候选片名。
        """
        text = str(subtitle or "")
        if not text:
            return []
        text = re.sub(r"\[[^\]]*\]|【[^】]*】|\([^)]*\)|（[^）]*）", " ", text)
        names: List[str] = []
        for seg in re.findall(r"[\u3400-\u9fff][\u3400-\u9fff0-9０-９：:·・]*", text):
            core = seg.strip(" ：:·・")
            for word in _CN_TAG_WORDS:
                core = core.replace(word, " ")
            core = re.sub(r"\s+", "", core)
            # 「2026年亚残运会开幕式」会在数字处断开，留下「年亚残运会开幕式」这种残片
            core = re.sub(r"^[年月日号期集话話]", "", core)
            if len(core) < 2 or len(core) > 30:
                continue
            if not re.search(r"[\u3400-\u9fff]", core):
                continue
            # 纯标签/纯季集号（如「12集全」「中字」）不是片名
            if re.fullmatch(r"[0-9０-９第季集期卷话話部共完结更新全]+", core):
                continue
            if not cls._informative(core):
                continue
            if core not in names:
                names.append(core)
        return names[:2]

    @staticmethod
    def _display_label(meta: Any) -> Tuple[str, str]:
        """MP 实际解析出来的片名，用于徽标 tooltip 的诊断展示。

        用户排查「明明库里有却判未入库」时，需要看到 MP 到底把这条发布名
        认成了什么 —— 所以这里**只取 MP 的解析结果**，按 cn_name → en_name
        → name 的顺序；拿不到就返回空（前端显示「未能解析出片名」），
        绝不退回整条发布名，也不拿副标题里的中文名充数（那只是搜索补丁）。
        """
        if meta is None:
            return "", ""
        for key in ("cn_name", "en_name", "name"):
            value = str(getattr(meta, key, None) or "").strip()
            if value:
                return value, key
        return "", ""

    @staticmethod
    def _name_score(candidate: str, names: List[str]) -> int:
        c = (candidate or "").strip()
        if not c:
            return 0
        best = 0
        for want in names:
            w = (want or "").strip()
            if not w:
                continue
            if c == w:
                return 100
            if (w in c or c in w) and PtLibraryCheck._containment_ok(c, w):
                best = max(best, 80)
                continue
            strip = lambda s: re.sub(r"[第S]\s*\d+[季部]?", "", s).replace(" ", "")  # noqa: E731
            if strip(c) == strip(w):
                best = max(best, 70)
            # 标点差异不算不同片名：PT 站的 `Avatar Fire and Ash` 在库里常写成
            # `Avatar: Fire and Ash`，硬比字符串会漏判
            if best < 95 and len(PtLibraryCheck._bare(c)) >= 2 \
                    and PtLibraryCheck._bare(c) == PtLibraryCheck._bare(w):
                best = 95
        return best

    @staticmethod
    def _bare(text: str) -> str:
        """去掉标点/空白并转小写，只留字母数字与汉字，用于宽松同名判断。"""
        return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", (text or "").lower())

    @staticmethod
    def _item_imdb(item: dict, emby: bool) -> str:
        """从 Emby/Plex 的条目里取 IMDb ID。"""
        if emby:
            ids = item.get("ProviderIds") or {}
            for key, value in ids.items():
                if str(key).lower() == "imdb" and value:
                    return _normalize_imdb(value)
            return ""
        guid = str(item.get("guid") or "")
        found = _normalize_imdb(guid)
        if found:
            return found
        for g in item.get("Guid") or []:
            found = _normalize_imdb((g or {}).get("id") if isinstance(g, dict) else g)
            if found:
                return found
        return ""

    def _pick_best(self, items: List[dict], names: List[str], year: Optional[str],
                   tmdb_id: Optional[str], emby: bool,
                   imdb: str = "") -> Optional[dict]:
        scored = self._pick_best_scored(items, names, year, tmdb_id, emby, imdb)
        return scored[0] if scored else None

    def _pick_best_scored(self, items: List[dict], names: List[str], year: Optional[str],
                          tmdb_id: Optional[str], emby: bool,
                          imdb: str = "") -> Optional[Tuple[dict, int, bool]]:
        """返回 (条目, 分数, 年份是否冲突)。

        分数语义：100 = 片名完全相同；95 = 去掉标点后相同；
        >=400 = imdb 身份命中（片名可以不像）；>=200 = TMDb 身份命中；
        80/70 = 包含/去季号后相同（模糊，需年份不冲突才算数）；
        <=0 视为不相关。
        """
        if not items:
            return None
        scored = []
        for it in items:
            if emby:
                # 中英文名都参与比对：库里的主名可能是译名，原名在 OriginalTitle 里
                cand_names = [it.get("Name"), it.get("OriginalTitle")]
                cand_year = it.get("ProductionYear")
                cand_tmdb = ((it.get("ProviderIds") or {}).get("Tmdb"))
            else:
                # Plex 搜索结果的 title 是显示名（可能是中文译名），
                # 原名在 originalTitle / titleSort 里 —— 只比 title 会漏掉一堆在库条目
                cand_names = [it.get("title"), it.get("originalTitle"), it.get("titleSort")]
                cand_year = it.get("year")
                guid = it.get("guid") or ""
                m = re.search(r"tmdb://(\d+)", guid)
                cand_tmdb = m.group(1) if m else None
            score = max((self._name_score(n, names) for n in cand_names if n), default=0)
            if imdb and self._item_imdb(it, emby) == imdb:
                # IMDb 是硬身份：命中就不再看片名像不像
                score += 400
            if tmdb_id and cand_tmdb and str(cand_tmdb) == str(tmdb_id):
                score += 200
            if score <= 0:
                continue
            conflict = bool(year and cand_year and str(cand_year) != str(year))
            if conflict:
                # 年份不符先留着，没有更合适的再用
                score -= 40
            scored.append((score, it, conflict))
        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1], scored[0][0], scored[0][2]

    @staticmethod
    def _accept_score(score: int, year_conflict: bool) -> bool:
        """够不够格认定「在库」。

        第二轮不再无条件接受最高分候选：同名不同片（年份差一年）只有 60 分，
        之前会被当成库内（实测《Tuner 2025》被判成库内《调音师 2026》）。
        所以只有「身份确认」或「片名完全/近乎完全相同」直接算，
        模糊匹配（包含、去季号）必须年份不冲突。
        """
        if score >= 200:      # TMDb / IMDb 身份命中
            return True
        if score >= 95:       # 片名完全相同（含仅标点差异）
            return True
        return score >= 70 and not year_conflict

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_imdb(value: Any) -> str:
        """把站点上的各种写法统一成 tt1234567（供外部/测试直接调用）。"""
        return _normalize_imdb(value)

    @staticmethod
    def _log(message: str) -> None:
        try:
            from app.sdk.logging import logger
        except ImportError:  # 旧宿主回退
            try:
                from app.runtime.log import logger
            except Exception:  # noqa: BLE001 - 日志失败不影响主流程
                return
        try:
            logger.info(f"[PT媒体库检测] {message}")
        except Exception:  # noqa: BLE001 - 日志失败不影响主流程
            pass
