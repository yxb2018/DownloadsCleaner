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

**为什么这么拆**（对比"油猴自己直连 Emby/Plex"）：

| | 直连版 | 插件版（本方案） |
|---|---|---|
| 密钥 | 3 把（Emby/Plex/MP）要写进脚本 | **0 把**。凭据全在 MP 侧 |
| 标题识别 | 自带正则 + 调 recognize | MP 原生 `MetaInfo` + TMDB 识别，与订阅同款 |
| 多服务器 | 只能连一个 | 遍历 MP 里配置的所有，逐个找 |
| 分集判断 | 自己查 episodes + PlaybackInfo | 插件直接列在库季集 |
| 油猴体积 | 717 行 | ~430 行，只管 DOM |
| GreasyFork 发布 | 发布前必须记得清空密钥 | 无需清理任何东西 |
| MP 挂了 | 还能查 Emby | 全挂 → 用直连版兜底 |

直连版保留为 `PT媒体库检测-直连版.user.js`，需要时把 `CONFIG` 里的密钥填上即可用。

---

## 二、文件清单

| 文件 | 说明 |
|---|---|
| `PT媒体库检测.user.js` | **主交付物**。瘦油猴客户端 v2.0.0，只存 MP 地址 |
| `PT媒体库检测-直连版.user.js` | 兜底版。自己直连 Emby/Plex，密钥默认全空 |
| `pt-plugin/plugins.v3/ptlibrarycheck/__init__.py` | MP 插件 `PtLibraryCheck` |
| `pt-plugin/package.v3.json` | 插件市场索引（key = 插件类名） |
| `tests/test_mp_plugin.py` | 插件单测（79 项，MP 模块全打桩） |
| `tests/test_direct.js` | 直连版单测（23 项） |
| `tests/test_userscript.js` | 瘦客户端单测（15 项） |

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
GET /api/v1/plugin/PtLibraryCheck/check?title=<标题>[&subtitle=<副标题>][&imdb=tt1234567][&season=1][&episode=9]
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

插件配置项（MP 插件页）：

| 项 | 默认 | 说明 |
|---|---|---|
| 启用插件 | 关 | |
| 允许匿名访问查询接口 | 关 | 打开后油猴无需 MP API Key |
| 查询剧集时返回具体分集是否在库 | 开 | |
| 查询缓存小时数 | 24 | 0 = 不缓存 |

---

## 五、风控说明

脚本**只读 PT 站点的页面 DOM**，不向 PT 站点发任何额外请求；
所有查询都打你自己的 MP，与站点风控无关。

仍然建议：

- 列表页一次会触发几十个查询，但全部走限流队列（默认并发 2、最小间隔 250ms），
  且 24 小时缓存，翻页重复项不会重复查
- 不要把 `behavior.autoDetectSite` 打开去跑未验证的站点

---

## 六、站点适配

内置 20 个国内 NexusPHP 站（不含学校站）：

```text
ourbits.club  pterclub.com  hdarea.club  mteam.cc  hdsky.me  chdbits.cid
pthome.net  soulvoice.club  hdhome.org  totheglory.im  springsunday.net
nicept.net  hhanclub.top  52pt.site  audiences.me  hdatmos.club  hdfans.org
ubits.club  pttime.org  pt.keepfrds.com
```

增删站点改脚本顶部：

```javascript
sites: {
  extra: ['pt.example.com'],   // 额外启用
  exclude: ['byr.pt'],         // 排除（优先于 extra）
},
behavior: { autoDetectSite: false },  // 学校站请保持 false
```

NexusPHP 的通用结构（`table.torrents` / `a[href*="details.php?id="]` / 标题单元格内的
副标题）已经覆盖绝大多数站点；个别魔改站点如果徽标位置不对，调
`collectListItems()` / `collectDetailItem()` 里的选择器即可。

---

## 七、测试

```bash
# MP 插件（把 MP 模块全打桩，不依赖真实 MP）
python tests/test_mp_plugin.py          # 101 项

# 油猴（Node 22 自带 test runner + jsdom）
node tests/test_direct.js               # 23 项
node tests/test_userscript.js           # 19 项
```

当前状态：**143 项全过**。

---

## 八、部署状态（2026-10-20 实测）

- 插件仓库：`github.com/yxb2018/DownloadsCleaner`（复用现有多插件仓库，
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

直连版 `PT媒体库检测-直连版.user.js` 含 `SECRETS` 区块，**不要发布**，
只作本机兜底。
