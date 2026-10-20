"""PT 媒体库检测插件。

给 PT 站点的油猴脚本提供一个查询接口：传入种子标题，返回该资源是否已在
MP 配置的媒体服务器（Emby / Plex / Jellyfin ...）中，以及在库版本的
分辨率 / 杜比视界 / HDR / 编码 / 音轨 / 字幕 / 路径。

设计要点：
- 插件复用 MP 已配置的媒体服务器凭据，**不在任何地方保存 Emby/Plex 密钥**；
- 标题识别直接用 MP 的 MetaInfo + MediaChain.recognize_by_meta，
  与订阅 / 媒体整理用的是同一套解析与 TMDB 识别；
- 画质信息通过媒体服务器模块自带的 get_data() 取原始 JSON 解析，
  MediaInfo / MediaServerItem 都不带分辨率与 DoVi 字段，只能走原始数据。
"""

import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from app.application.mediaserver import MediaServerHelper
from app.chain.media import MediaChain
from app.domain.metainfo import MetaInfo
from app.schemas.types import MediaType
from app.sdk.plugin import _PluginBase

# 查询结果缓存 key 前缀
_CACHE_PREFIX = "ptmc_chk_"
# 单次查询最多返回的在库季集数量（避免响应体过大）
_MAX_SEASONS = 20


class PtLibraryCheck(_PluginBase):
    """PT 站点媒体库存在性与画质查询。"""

    plugin_name = "PT媒体库检测"
    plugin_desc = "为 PT 站点油猴脚本提供媒体库存在性与画质查询（复用 MP 已配置的媒体服务器）"
    plugin_version = "1.0.0"
    plugin_author = "local"
    plugin_label = "媒体库"
    plugin_order = 100
    plugin_config_prefix = "ptlibrarycheck_"
    # 1 = 普通权限；2 需要站点认证等级，未装配站点能力时会被 runtime 策略拦住
    auth_level = 1

    _enabled = False
    _allow_anonymous = False
    _cache_hours = 24
    _episode_check = True

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def init_plugin(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.stop_service()
        self._enabled = False
        self._allow_anonymous = False
        self._cache_hours = 24
        self._episode_check = True
        if not config:
            return
        self._enabled = bool(config.get("enabled"))
        self._allow_anonymous = bool(config.get("allow_anonymous"))
        try:
            self._cache_hours = max(0, int(config.get("cache_hours") or 24))
        except (TypeError, ValueError):
            self._cache_hours = 24
        self._episode_check = bool(config.get("episode_check", True))

    def get_state(self) -> bool:
        return self._enabled

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
                ],
            }
        ], {
            "enabled": False,
            "allow_anonymous": False,
            "episode_check": True,
            "cache_hours": 24,
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
                "description": "传入种子标题（可带副标题），返回存在性、在库版本画质与分集情况",
            }
        ]

    async def check(self,
                    title: str = "",
                    subtitle: str = "",
                    season: Optional[int] = None,
                    episode: Optional[int] = None) -> Dict[str, Any]:
        """查询入口。阻塞的媒体服务器调用放到线程池，避免卡住事件循环。"""
        if not self._enabled:
            return {"success": False, "message": "插件未启用"}
        title = (title or "").strip()
        if not title:
            return {"success": False, "message": "缺少 title 参数"}
        import asyncio
        try:
            data = await asyncio.to_thread(
                self._query, title, (subtitle or "").strip(), season, episode
            )
        except Exception as err:  # noqa: BLE001 - 接口不能把异常抛给前端
            return {"success": False, "message": f"查询失败：{err}"}
        return {"success": True, "data": data}

    # ------------------------------------------------------------------
    # 查询主流程
    # ------------------------------------------------------------------
    def _query(self, title: str, subtitle: str,
               want_season: Optional[int], want_episode: Optional[int]) -> Dict[str, Any]:
        # 缓存
        cache_key = None
        if self._cache_hours > 0:
            raw = f"{title}\n{subtitle}|{want_season}|{want_episode}"
            cache_key = _CACHE_PREFIX + str(abs(hash(raw)))
            hit = self.get_data(cache_key)
            if isinstance(hit, dict) and hit.get("_t"):
                if time.time() - hit["_t"] < self._cache_hours * 3600:
                    return hit["v"]

        result = self._query_uncached(title, subtitle, want_season, want_episode)

        if cache_key:
            try:
                self.save_data(cache_key, {"_t": time.time(), "v": result})
            except Exception:  # noqa: BLE001 - 缓存失败不影响查询
                pass
        return result

    def _query_uncached(self, title: str, subtitle: str,
                        want_season: Optional[int], want_episode: Optional[int]) -> Dict[str, Any]:
        # 1. 标题解析（MP 原生 MetaInfo）
        meta = MetaInfo(title, subtitle or None)
        is_tv = meta.type == MediaType.TV or bool(meta.begin_season)
        season = want_season if want_season is not None else meta.begin_season
        episode = want_episode if want_episode is not None else meta.begin_episode

        # 2. TMDB 识别（拿不到就用名称+年份匹配）
        tmdb_id = None
        try:
            mediainfo = MediaChain().recognize_by_meta(meta)
            if mediainfo and mediainfo.tmdb_id:
                tmdb_id = str(mediainfo.tmdb_id)
        except Exception as err:  # noqa: BLE001 - 识别失败不该阻断查询
            self._log(f"TMDB 识别失败：{err}")

        names = self._candidate_names(meta)
        if not names:
            return {"found": False, "reason": "无法解析片名", "title": title}

        # 3. 遍历 MP 里配置的所有媒体服务器
        helper = MediaServerHelper()
        services = helper.get_services()
        if not services:
            return {"found": False, "reason": "MP 未配置媒体服务器", "title": title}

        errors: List[str] = []
        for name, svc in services.items():
            server = svc.instance
            stype = (svc.type or "").lower()
            if not server:
                continue
            try:
                if stype in ("emby", "jellyfin"):
                    hit = self._check_emby(server, names, meta.year, tmdb_id,
                                           is_tv, season, episode)
                elif stype == "plex":
                    hit = self._check_plex(server, names, meta.year, tmdb_id,
                                           is_tv, season, episode)
                else:
                    continue
            except Exception as err:  # noqa: BLE001 - 单个服务器失败不影响其他
                errors.append(f"{name}: {err}")
                continue
            if hit:
                hit["server"] = name
                hit["server_type"] = stype
                return hit

        reason = "媒体库无此资源"
        if errors:
            reason += "（" + "；".join(errors[:2]) + "）"
        return {"found": False, "reason": reason, "title": title}

    # ------------------------------------------------------------------
    # Emby / Jellyfin
    # ------------------------------------------------------------------
    def _check_emby(self, server: Any, names: List[str], year: Optional[str],
                    tmdb_id: Optional[str], is_tv: bool,
                    season: Optional[int], episode: Optional[int]) -> Optional[Dict[str, Any]]:
        item_type = "Series" if is_tv else "Movie"
        items: List[dict] = []
        seen = set()
        for term in names[:3]:
            url = (
                f"[HOST]emby/Items?Recursive=true&IncludeItemTypes={item_type}"
                f"&searchTerm={quote(term)}&Limit=20"
                f"&Fields=MediaSources,MediaStreams,ProviderIds,ProductionYear,"
                f"OriginalTitle,Path&api_key=[APIKEY]"
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
                    f"&searchTerm={quote(bare)}&Limit=20"
                    f"&Fields=MediaSources,MediaStreams,ProviderIds,ProductionYear,"
                    f"OriginalTitle,Path&api_key=[APIKEY]"
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

        best = self._pick_best(items, names, year, tmdb_id, emby=True)
        if not best:
            return None

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
                    season: Optional[int], episode: Optional[int]) -> Optional[Dict[str, Any]]:
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

        best = self._pick_best(candidates, names, year, tmdb_id, emby=False)
        if not best:
            return None

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
    def _candidate_names(meta: Any) -> List[str]:
        """按优先级给出可用于搜索与比对的名称列表。"""
        names: List[str] = []
        for value in (
            getattr(meta, "cn_name", None),
            getattr(meta, "en_name", None),
            getattr(meta, "original_name", None),
            getattr(meta, "title", None),
        ):
            if not value:
                continue
            value = str(value).strip()
            if not value or value in names:
                continue
            # 去掉季集后缀，避免搜索词带季号
            value = re.sub(r"[第S]\s*\d+\s*[季部]?", "", value).strip()
            if value and value not in names:
                names.append(value)
        return names

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
            if w in c or c in w:
                best = max(best, 80)
                continue
            strip = lambda s: re.sub(r"[第S]\s*\d+[季部]?", "", s).replace(" ", "")  # noqa: E731
            if strip(c) == strip(w):
                best = max(best, 70)
        return best

    def _pick_best(self, items: List[dict], names: List[str], year: Optional[str],
                   tmdb_id: Optional[str], emby: bool) -> Optional[dict]:
        if not items:
            return None
        scored = []
        for it in items:
            if emby:
                cand_name = it.get("Name") or it.get("OriginalTitle") or ""
                cand_year = it.get("ProductionYear")
                cand_tmdb = ((it.get("ProviderIds") or {}).get("Tmdb"))
            else:
                cand_name = it.get("title") or ""
                cand_year = it.get("year")
                guid = it.get("guid") or ""
                m = re.search(r"tmdb://(\d+)", guid)
                cand_tmdb = m.group(1) if m else None
            score = self._name_score(cand_name, names)
            if tmdb_id and cand_tmdb and str(cand_tmdb) == str(tmdb_id):
                score += 200
            if score <= 0:
                continue
            if year and cand_year and str(cand_year) != str(year):
                # 年份不符先留着，没有更合适的再用
                score -= 40
            scored.append((score, it))
        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    @staticmethod
    def _log(message: str) -> None:
        try:
            from app.runtime.log import logger
            logger.info(f"[PT媒体库检测] {message}")
        except Exception:  # noqa: BLE001 - 日志失败不影响主流程
            pass
