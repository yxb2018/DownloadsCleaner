# PT媒体库检测（PtLibraryCheck）

给 PT 站点的油猴脚本提供媒体库存在性与画质查询：传入种子标题（或站点已填的
IMDb ID），返回该资源是否已在 MoviePilot 配置的媒体服务器（Emby / Plex /
Jellyfin）中，以及在库版本的分辨率 / 杜比视界 / HDR / 编码 / 音轨 / 字幕 / 路径。

- 插件复用 MP 已配置的媒体服务器凭据，不在任何地方保存 Emby/Plex 密钥；
- 识别优先用 `imdb` 参数（站点种子页已填的 IMDb ID，最准也最快），
  没有 imdb 才退回 MetaInfo 标题识别，且识别有硬超时（默认 6s），不会拖死请求；
- 配套油猴脚本见仓库根目录 `userscripts/`。

接口：`GET/POST /api/v1/plugin/PtLibraryCheck/check`

| 参数 | 说明 |
| --- | --- |
| `title` | 种子标题（发布名） |
| `subtitle` | 副标题（可选） |
| `imdb` | `tt1234567` 或完整 IMDb 链接（可选） |
| `season` / `episode` | 指定季集（可选，缺省从标题解析） |
| `debug` | `1` 时返回解析结果、各阶段耗时与匹配置信度（不写缓存） |

版本历史见仓库根目录 `package.v3.json`。
