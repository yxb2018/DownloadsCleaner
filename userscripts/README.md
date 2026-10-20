# PT 媒体库检测

在 PT 站点（NexusPHP 系）的种子列表页 / 详情页，标注该资源是否已在你的媒体库里，
并显示在库版本的分辨率、杜比视界（DoVi）、HDR、编码、音轨、字幕和路径。

```
列表页：  [库内 2160p DoVi]  [未入库]  [库内·缺 S01E09]  [查询失败]
详情页：  ✓ 已在媒体库 ｜ 媒体服务器 Breeze-NAS ｜ 画质 2160p 杜比视界 DoVi hevc
          ｜ 音轨 … ｜ 字幕 … ｜ 在库季集 S01(3集) S02(2集) ｜ 路径 …
```

---

## 一、架构：MP 插件 + 瘦油猴

```
┌──────────────┐   GET /api/v1/plugin/PtLibraryCheck/check?title=..
│  油猴脚本     │ ───────────────────────────────────────────────▶
│  (只改 DOM)   │ ◀───────────────────────────────────────────────
└──────────────┘   {found, quality:{resolution,dv,hdr,codec,audio,subtitles,path},
                     seasons:{季:[集]}, episode:{found}, server, reason}
                          │
                          ▼
                 ┌─────────────────┐     ┌──────────────┐
                 │ MP 插件         │────▶│ Emby / Plex  │  凭据用 MP 里已配置的
                 │ PtLibraryCheck  │     │ (MP 已连好)   │
                 └─────────────────┘     └──────────────┘
```

**为什么这么拆**（油猴不自己连 Emby/Plex）：

- **密钥只有 0 把**：Emby/Plex 凭据全留在 MP 侧，脚本里没有可泄漏的东西
- **识别用 MP 原生链路**：`MetaInfo` 解析 + TMDb 识别，跟订阅/整理同款，不会各说各话
- **多服务器**：遍历 MP 里配置的所有媒体服务器，逐个找
- **分集判断**：插件直接列在库季集，油猴不用自己拼 episodes 请求
- **油猴只管 DOM**：体积小、改样式不动数据层

> 早期的「直连版」（油猴自己连 Emby/Plex）已废弃并下架，本仓库只维护插件版。

---

## 二、文件清单

| 文件 | 说明 |
|---|---|
| `userscripts/PT媒体库检测.user.js` | **主交付物**。瘦油猴客户端，只存 MP 地址 |
| `plugins.v3/ptlibrarycheck/__init__.py` | MP 插件 `PtLibraryCheck` |
| `package.v3.json` | 插件市场索引（key = 插件类名） |
| `tests/test_mp_plugin.py` | 插件单测（218 项，MP 模块全打桩） |
| `tests/test_userscript.js` | 瘦客户端单测（37 项） |

---

## 三、安装

### 1. 装 MP 插件（一次性）

MP 的插件安装只认仓库地址（`GET /api/v1/plugin/install/{插件ID}?repo_url=...`），
MP 会去读仓库根目录的 `package.v3.json`，再用 GitHub Contents API 拉
`plugins.v3/ptlibrarycheck/` 下的文件。

```text
MP → 插件市场 → 添加仓库 https://github.com/<user>/<repo> → 找到「PT媒体库检测」→ 安装
```

约束（不满足会装不上或找不到）：

- 仓库**默认分支必须是 `main`**，根目录有 `package.v3.json`，其中有一条 `"PtLibraryCheck": {...}`
- 插件代码在 `plugins.v3/ptlibrarycheck/__init__.py`（目录名 = 类名小写）
- 索引里**不要写 `"release": true`** —— 写了 MP 会去找 `PtLibraryCheck_v1.0.0` 的
  GitHub Release；不写则走文件列表安装，不需要发 Release
- **私有仓库**：MP 设置里要补 `REPO_GITHUB_TOKEN`，格式
  `{user}/{repo}:ghp_xxxxxxxx`（多个仓库用逗号分隔）。公开仓库可以不配，
  但会受 GitHub 匿名速率限制（Contents API 60 次/小时，装一个插件够用）
- 装完在插件页把「启用插件」打开

**市场索引缓存坑（实测 2026-10-20）**：仓库里新加了插件条目后，如果安装报
「没有找到插件 X 的可用安装包」，是 MP 的市场库存快照还是旧的。先强制刷新：

```bash
curl -H "X-API-KEY: <key>" "https://<mp>/api/v1/plugin/source/PtLibraryCheck/options?force=true"
```

返回 `selection_status: "selected"` 且 candidates 里出现该仓库后，再安装即可。
（仓库已在 MP 市场列表里时不需要重新添加仓库。）

**排查"插件加载失败"**：安装成功但加载失败时，99% 是 import 或抽象方法问题
（详见第九节开发备注）。MP 无 API 可读插件日志，用 `POST /api/v1/plugin/reload/{id}`
可以拿到分类错误信息。

### 2. 装油猴脚本

Tampermonkey → 新建脚本 → 粘贴 `PT媒体库检测.user.js` 全文 → 保存。

然后在站点页面点油猴菜单 → **`① 设置 MP 地址与 API Key`**，填入你的 MP 地址
（如 `https://mp.example.com:3000`，不带结尾斜杠）。地址与 Key 都存在 Tampermonkey
自己的存储里，**脚本文件里不内置任何域名或密钥**，可直接分享/发布。

> 地址没填时脚本不插入样式也不发请求，只在控制台打一行提示。

### 3. 密钥（可选）

插件配置页有个开关「允许匿名访问查询接口」：

- **打开** → 油猴只填地址即可，不用 API Key
- **关闭**（默认，更安全）→ 油猴菜单 → `① 设置 MP 地址与 API Key` 里一并填 Key，
  存在 Tampermonkey 存储里，**不会写进脚本文件**

> MP API Key 在 MP「设置 → 安全」里能看到。

---

## 四、插件接口

```http
GET /api/v1/plugin/PtLibraryCheck/check?title=<标题>[&subtitle=<副标题>][&imdb=tt1234567][&season=1][&episode=9][&priority=high][&lite=1]
```

**识别顺序（v1.1.0 起）**：`imdb` 优先 —— 站点种子行里如果已填 IMDb
（HDSky 等站是 `<a href="http://www.imdb.com/title/tt14205554"><img src="/pic/icon-imdb.png"></a>`
这个图标链接），油猴直接把 `tt号` 传进来，插件用 IMDb 做身份识别拿到条目，
不会认错同名作品；Emby / Jellyfin 还会用 `AnyProviderIdEquals=imdb.ttXXXX`
直接定位，省掉按片名搜索。站点没填 IMDb 时才退回「标题 + 副标题」识别。

`imdb` 兼容各种写法：`tt14205554`、`TT14205554`、`http://www.imdb.com/title/tt14205554`
都会被归一化成 `tt14205554`。

返回：

```json
{
  "success": true,
  "data": {
    "found": true,
    "type": "tv",
    "title": "怪奇物语",
    "year": "2016",
    "server": "Breeze-NAS",
    "server_type": "emby",
    "quality": {
      "resolution": "2160p", "dv": true, "hdr": "DOVI", "codec": "hevc",
      "audio": ["DDP 5.1"], "subtitles": ["简体中文"],
      "src_name": "Stranger.Things.S01E01...", "path": "/data/media/tv/..."
    },
    "seasons": {"1": [1, 2, 3]},
    "episode_count": 3,
    "episode": {"season": 1, "episode": 1, "found": true},
    "imdb": "tt14205554",
    "matched_by": "imdb",
    "reason": ""
  }
}
```

`imdb` 为站点传入并归一化后的 IMDb ID；`matched_by` 为 `imdb`（IMDb 识别命中）
或 `title`（标题识别命中），方便前端显示识别方式。

`found: false` 时 `reason` 说明原因（媒体库无此资源 / 年份不匹配 / 无法解析片名 /
未配置媒体服务器 …）。

> `seasons` 为控制响应体大小，每季最多回 20 集；`episode_count` 是该季在库真实
> 总数（油猴显示为 `S01(20+/40集)`）。`episode.found` 用完整列表判断，不受截断影响。

```http
GET /api/v1/plugin/PtLibraryCheck/sites
```

返回 MP 站点表里的站点名与域名（供油猴判断「当前页面适不适用」）：

```json
{"success": true, "data": {
  "count": 92, "active": 78,
  "sites": [{"name": "彩虹岛", "domain": "ptchdbits.co",
             "hosts": ["ptchdbits.co"], "active": true}]
}}
```

`hosts` 同时从 `domain` 字段与站点 URL 的主机名推导（去掉 `www.`），
空域名条目自动跳过；结果在插件内存里缓存 5 分钟。

插件配置项（MP 插件页）：

| 项 | 默认 | 说明 |
|---|---|---|
| 启用插件 | 关 | |
| 允许匿名访问查询接口 | 关 | 打开后油猴无需 MP API Key |
| 查询剧集时返回具体分集是否在库 | 开 | |
| 查询缓存小时数 | 24 | 0 = 不缓存 |
| 并发上限 max_concurrent | 3 | 所有标签页共用的进程级闸门 |
| 排队等待上限 gate_timeout | 25 | 秒；等超了返回 `busy: true` |

---

## 五、风控说明

脚本**只读 PT 站点的页面 DOM**，不向 PT 站点发任何额外请求；
所有查询都打你自己的 MP，与站点风控无关。

仍然建议：

- 列表页一次会触发几十个查询，但全部走限流队列（默认并发 2、最小间隔 250ms），
  且 24 小时缓存，翻页重复项不会重复查
- 不要把 `behavior.autoDetectSite` 打开去跑未验证的站点

---

## 五之二、多标签页并发与优先级队列

**问题**：油猴是「每个标签页各跑一份」，页内限流管不住跨页并发 ——
同时开 5 个站刷列表，就是 5 倍请求同时打进 MP，反代容易被拖到 504。

**做法**：真正兜住的是插件进程里的那道闸（`_ConcurrencyGate`），
油猴自己只做第一层。

| 层 | 位置 | 默认 | 作用 |
|---|---|---|---|
| 页内队列 | 油猴 `queue` | 并发 2 / 间隔 250ms | hot（详情页）优先于 normal（列表页）出队 |
| 进程闸门 | 插件 `_gate` | 并发 3 / 排队 25s | **所有标签页共用**，超了按优先级排队 |
| 同题合并 | 插件 `_inflight` | — | 多个标签页问同一部片，只真查一次 |
| 结果缓存 | 插件 `data` | 24h | 同一条重复出现不再查 |

优先级：详情页 / 手动操作传 `priority=high`，列表页默认 `normal`；
插队只在**排队时**生效（已经在跑的查询不会被抢占）。

闸门排满且等满 `gate_timeout` 时，`/check` 返回：

```json
{"success": true, "data": {
  "found": false, "timeout": true, "busy": true,
  "reason": "查询排队超时（并发上限 3，等满 25s）"
}}
```

`busy: true` 表示「没查到，不是没有」。油猴据此显示橙色「排队中」并自动重试
（4s、8s，共 2 次），**不写缓存**，绝不会把排队超时记成「未入库」。

### 两级查询：列表页只问「在不在」，详情页才要详情

`lite=1` 是列表页用的浅查询 —— **只用搜索结果本身判断，不再为细节多打媒体服务器**：

| | lite=1（列表页） | lite=0（详情页） |
|---|---|---|
| Emby 搜索 | 1 次 | 1 次 |
| Emby 分集列表 / 分集画质 | 不查 | 查（剧集各 +1~2 次） |
| Plex `/library/metadata/<id>` | 不查 | 查 |
| Plex 季集 children | 不查 | 查 |
| 返回字段 | `found/type/title/year/item_id` | 再加 `quality/seasons/episode/path` |
| 剧集缺集提示 | 「库内」 | 「库内·缺 SxxExx」 |

一条剧集列表行从最多 3 次媒体服务器请求降到 1 次。

油猴的配合方式：

- 列表页 `lite=1`；详情页 `lite=0`，且**先读列表页那趟的结论把面板铺上**
  （`peekCached`，同步读本地缓存，不重新请求、不重新识别），再补全画质 / 季集。
- 缓存按 `|full` / `|lite` 分开：lite 请求可以吃 full 的结论（超集），
  **full 请求绝不吃 lite 的结论**（否则详情页永远补不全）。
- full 成功后顺手把 lite 那份也升级掉，所以打开过一次详情页的行，回列表页直接显示完整结论（含缺集）。

同题合并命中时返回体多一个 `coalesced: true`（表示结果来自别的标签页的同一次查询，
不是自己那份数据的重新计算）。

### 后台标签页的坑（v2.2.6）

Chromium 内核（Chrome / **Edge**）对隐藏标签页会节流定时器——后台 5 分钟后
`setTimeout` 降到约 1 次/分钟；Edge 还有「睡眠标签页」，超时后整页冻结、
JS 一行都不跑。表现为：**列表只落地 3~4 条，其余一直「查询中…」**，
而 MP 侧闸门是空闲的（请求根本没发出去）。

脚本侧的处理：

- 0 延迟调度改用 `MessageChannel`（普通任务，不参与定时器节流），不再靠 `setTimeout` 排队；
- 后台标签页不再强加 250ms 请求间隔（可见时照旧节流），切回来能立刻追平；
- 整页冻结（睡眠标签页）是浏览器行为，页内代码无法自救——把 PT 站点加进
  Edge「设置 → 系统和性能 → 睡眠标签页 → 永不休眠」即可。

排查用：

```http
GET /api/v1/plugin/PtLibraryCheck/stats
{"success": true, "data": {"limit": 3, "active": 2, "waiting": 1,
                           "peak": 3, "rejected": 0, "gate_timeout": 25}}
```

`rejected` 就是「排队超时被拒」的累计次数 —— 这个数一直涨，说明该调大
并发上限或减少同时开着的标签页。

**关于「复用 MP 缓存、少打 TMDb」**：MP 自己的 `TmdbCache`
（`app/modules/themoviedb/cache.py`，TTL = 媒体元数据缓存天数）本来就拦着
重复的 TMDb 请求，插件识别时显式传 `cache=True` 走它。所以：

- 同一部片反复出现 → 命中 MP 的 TMDb 缓存，不产生新的 TMDb 调用；
- 第一轮只用本地 `MetaInfo` 解析出的片名搜媒体库，**片名完全相同就返回**，
  只有第一轮没找准才做标题识别（硬超时 6s）——大部分查询根本不碰 TMDb；
- 插件自己还有 24h 结果缓存（key 带插件版本号），重复标题连媒体服务器都不查。

---

## 六、站点适配

**只适配这几个站**，其它站一律不跑：

| 站点 | 域名 |
|---|---|
| 天空 HDSKY（参照实现） | `hdsky.me` |
| 彩虹岛 CHDBits（岛） | `ptchdbits.co` |
| 春天 Spring Sunday | `springsunday.net` |
| 猫站 PterClub | `pterclub.net` / `pterclub.com` |
| 我堡 OurBits | `ourbits.club` |
| 观众 Audiences | `audiences.me` |
| 憨憨 HHanClub | `hhanclub.net` / `hhanclub.top` |
| 听听歌 TTG | `totheglory.im`（结构特殊：标题链接 `/t/<id>/`，见下） |

**域名以 MP 站点表为准**：脚本启动时调插件 `GET /api/v1/plugin/PtLibraryCheck/sites`，
按 MP 里的**站点名**（彩虹岛 / 春天 / 猫站 / 我堡 / 观众 / 憨憨 / 听听歌 / 天空）取出对应域名。
所以站点改域名（`hhanclub.top` → `hhanclub.net`）**只需在 MP 里改，脚本不用重新发版**；
拉不到时退回脚本内置的同名域名清单。

增删适配站改脚本顶部（`ADAPTED_SITE_NAMES` 用 MP 站点名，`BUILTIN_SITES` 是兜底域名）：

```javascript
const ADAPTED_SITE_NAMES = ['天空', '彩虹岛', '春天', '猫站', '我堡', '观众', '憨憨', '听听歌'];
sites: {
  extra: ['pt.example.com'],   // 临时启用某域名（站点换了域名但 MP 还没改时用）
  exclude: ['hdsky.me'],       // 临时关掉名单里的某个站（优先级最高）
},
```

### 各站 DOM 差异（2026-10-20 逐站实测）

| 站点 | 列表结构 | 副标题容器 | 取副标题前要剔掉的装饰 | 备注 |
|---|---|---|---|---|
| 天空 HDSKY | `table.torrents`，标题在嵌套 `td.embedded` | 整格文本 | `.optiontag` | IMDb 链接覆盖率约 73% |
| 彩虹岛 CHDBits | `table.torrents`，每行 2 个 `td.embedded` | `font.subtitle` | `.tag` / `.hot` / `.circle` | 列表页**没有** IMDb 链接，只能靠标题+副标题 |
| 春天 Spring Sunday | `table.torrents` | `div.torrent-smalldescr` | `span.torrent-tag`（CMCTV/官方/中字/HDR10+） | |
| 猫站 PterClub | `table.torrents` | 整格文本 | `a.chs_tag` | 副标题混着促销倒计时，由 `cleanSubtitle()` 清 |
| 我堡 OurBits | `table.torrents`，5 个 `td.embedded` | 整格文本 | — | td0 带 `[热门]` 角标与「剩余时间：2月25天」 |
| 观众 Audiences | `table.torrents torrents-table` | 整格文本 | — | 列表页有 IMDb 链接（约 40%），走 IMDb 直查 |
| 憨憨 HHanClub | **不是 table**：`div.torrents-table` + `div.torr*` | `div.torrent-info-text-small_name`（截到第一个 `\|` 之前） | — | 详情页**没有 h1**，标题/副标题在 `div.grid` 的「标签 + 值」格里 |
| 听听歌 TTG | `table#torrent_table`，id 在 `<tr id>` | `<b>` 里 `<br>` 之后 | — | 见下表 |

憨憨的详情页靠 `gridCellByLabel('标题' / '副标题')` 从「标签 + 值」格里取，
其余站仍走 `h1` + `#subtitle`；两者都失败时退到 `document.title` 引号里的片名。

想验证某站的适配效果（采到几条、命中几条），把页面采集结果喂给插件统计即可：

```bash
# cdp_dump_items.js 在页面里导出 collectListItems()，再批量查 MP
MP_KEY=<你的 API Key> node tests/check_items.js items-hh.json
```

**听听歌 TTG 是例外**，它和 NexusPHP 通用结构完全不同：

| | NexusPHP 通用 | TTG |
|---|---|---|
| 种子 id | 标题链接 `details.php?id=123` 的 query | `<tr id="838510">` 的行 id |
| 标题链接 | `details.php?id=123&hit=1` | **`/t/838510/`**（`details.php` 的链接全是 filelist/做种/下载/评论计数） |
| 中文名 | 独立副标题元素（`font.subtitle` 等） | 与发布名同在 `<b>` 里，用 `<br>` 分隔 |
| IMDb | `a[href*="imdb.com/title/tt"]` | 同左（`.name_right` 的 `imdb_rate`） |

所以 `collectListItems()` 按站点分派：TTG 走 `collectTtgItems()`（按 `<tr id>` 取行、
`splitByBr()` 切标题/中文名、徽标插在 `<b>` 之后避免继承加粗），其余走 `collectNexusItems()`。

---

## 七、测试

```bash
# MP 插件（把 MP 模块全打桩，不依赖真实 MP）
python tests/test_mp_plugin.py          # 218 项

# 油猴（Node 自带 test runner + jsdom）
node tests/test_userscript.js           # 44 项
```

当前状态：**255 项全过**。

---

## 八、部署状态（2026-10-20 实测）

- 插件仓库：`github.com/yxb2018/MoviePilot-Plugins`（复用现有多插件仓库，
  `plugins.v3/ptlibrarycheck/` + 根索引 `package.v3.json` 内有 `PtLibraryCheck` 条目）
- 已部署实例（地址略，见下方说明）安装并启用后，接口
  `GET /api/v1/plugin/PtLibraryCheck/check` 实测：
  - 电影《功夫熊猫3》→ 命中 Plex（Breeze-NAS），1080p SDR hevc，3 条音轨 + 字幕列表
  - 剧集《墨雨云间》S01E09 → 命中 Plex，`episode_count: 40`，E09 在库
  - 无密钥访问 → `apikey 校验不通过`；同标题二次查询 → 命中缓存
- **油猴脚本侧**：装好脚本 → 菜单 `① 设置 MP 地址与 API Key`
  （或用菜单 `③ 测试连通性` 验证）
- 真实域名 / API Key **不写入任何可发布文件**，只存在于本机 Tampermonkey 存储

---

## 九、开发备注（MP v3.1.4 插件系统实证）

写 MP v3.1.4 插件踩过的坑，按源码核对过：

- `_PluginBase`（`app/sdk/plugin/base.py`）是 **ABCMeta**，6 个抽象方法
  （`init_plugin` / `get_state` / `get_api` / `get_form` / `get_page` / `stop_service`）
  少一个就实例化失败，表现为 MP 侧「插件加载失败」
- `MetaInfo`（`app/domain/metainfo.py`）是**工厂函数不是类**，返回
  `MetaVideo`/`MetaAnime`/`MetaMusic`；`grep "class MetaInfo"` 会误判为不存在
- `MediaServerHelper().get_services()` → `Dict[str, ServiceInfo]`，
  `ServiceInfo.instance` 是服务实例（Emby/Plex 对象），`get_data(url)` 在
  服务实例上（`[HOST]`/`[APIKEY]` 占位符替换），Plex 自动带 `X-Plex-Token`
- 插件接口 `allow_anonymous: True` → 宿主不加鉴权依赖（`app/adapters/web/plugin/routes.py`），
  可实现油猴零密钥
- **插件查询缓存会跨版本存活**：卸载不删 `plugindata` 表数据，24h 缓存会让
  旧代码的错结果掩盖新代码。改完代码重装后，用
  `GET /api/v1/plugin/reset/{id}` 清数据再测

---

## 十、发布到 GreasyFork

主脚本 `PT媒体库检测.user.js` **不含任何密钥**，可直接发布。
发布前仍建议自查一遍：

```bash
grep -nE "(apikey|api_key|token)" PT媒体库检测.user.js | grep -v "^\s*//"
```

只应看到 `CONFIG.mp.apiKey: ''`（空占位）和菜单/存储相关的变量名，
不应出现任何一串真实密钥。

工作区里的脚本是「干净版」（`host` / `apiKey` 都是空串），
本机安装副本另存于 `.workbuddy/private/tm-install-local.user.js`，不要提交。
