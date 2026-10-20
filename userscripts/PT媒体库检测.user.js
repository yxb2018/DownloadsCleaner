// ==UserScript==
// @name         PT 媒体库检测（MoviePilot 插件版）
// @namespace    https://local.ptmediacheck
// @version      2.3.1
// @description  在 PT 站点种子列表/详情页标注媒体库是否已有该资源；优先用站点已填的 IMDb ID 识别（最准最快），没有才解析标题+副标题。列表页标「库内 / 未入库」两种状态，详细信息在详情页展示。查询全部走 MoviePilot 的 PtLibraryCheck 插件，油猴本身不保存 Emby/Plex 密钥。
// @author       yxb2018
// @license      MIT
// @match        *://*.audiences.me/*
// @match        *://*.hdsky.me/*
// @match        *://*.hhanclub.net/*
// @match        *://*.hhanclub.top/*
// @match        *://*.ourbits.club/*
// @match        *://*.ptchdbits.co/*
// @match        *://*.pterclub.com/*
// @match        *://*.pterclub.net/*
// @match        *://*.springsunday.net/*
// @match        *://*.totheglory.im/*
// @connect      *
// 说明：MP 地址由用户在油猴菜单里自行填写，脚本不内置任何具体域名，
//      因此这里只能用通配 @connect；如你不放心，可改成你自己的 MP 域名。
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_deleteValue
// @grant        GM_listValues
// @grant        GM_registerMenuCommand
// @grant        unsafeWindow
// @run-at       document-idle
// ==/UserScript==

/*
 * =====================================================================
 * PT 媒体库检测 · MoviePilot 插件版（v2.3.1）
 * ---------------------------------------------------------------------
 * 架构：本脚本只负责「读页面 DOM + 调 MP 插件接口 + 渲染徽标/面板」。
 *      媒体库存在性、画质、分集判断全部由 MP 的 PtLibraryCheck 插件完成，
 *      凭据用的是 MP 里已配置的媒体服务器，所以：
 *        · 本脚本不需要 Emby / Plex 的任何密钥；
 *        · 只有 MP 插件开启「允许匿名访问」时，才连 MP API Key 都不用填。
 *
 * v2.3.1
 *   · 修 TTG 详情页整页不渲染：TTG 的详情页是 /t/<种子ID>/（列表页才是
 *     /browse.php），而 isDetailPage() 只认 details.php，于是面板压根不插入。
 *     现在按「主机是 totheglory.im + 路径 /t/数字」判定（限定主机，别站不误判）。
 *   · 配套插件 1.1.13 修「列表页显示库内、详情页却显示未找到」。
 *
 * v2.3.0
 *   · 详情页面板跟着站点布局走（不再是一块自带边框的外来卡片）：
 *     - NexusPHP（天空/彩虹岛/春天/猫站/我堡/观众/TTG）：用站点自己的
 *       <table class="main"> + td.rowhead / td.rowfollow / td.colhead 渲染，
 *       看起来就是页面自带的一块字段区
 *     - 憨憨这类新版前端（div.grid 的「标签 + 值」两列）：面板插进 grid 里，
 *       grid-column:1/-1 横跨两列，内部也是「标签 + 值」
 *     - 认不出来时退回原来的卡片（plain）
 *   · 详情页新增 Plex / Emby / Jellyfin 播放按钮（配套插件 1.1.12）：
 *     库内才显示；两台服务器都有时两个都给；图标是各自官方配色的内联 SVG
 *     （Plex #cc7b19 / Emby #52b54b / Jellyfin #aa5cc3），不外链图片。
 *     链接由插件按媒体服务器配置生成，脚本里不存任何媒体服务器地址或密钥。
 *
 * v2.2.6
 *   · 修：后台标签页的列表整体卡在「查询中…」（憨憨 100 行只落地 3~4 条，
 *     我堡/观众/春天/猫站同样症状）。实测 Chrome 对隐藏标签页的 setTimeout
 *     有节流——后台 5 分钟后降到约 1 次/分钟，而队列的请求间隔正是用
 *     setTimeout 排的，于是队列停摆（MP 侧闸门却是空闲的，说明请求根本没发出去）。
 *     现在 0 延迟调度改用 MessageChannel（普通任务，不参与定时器节流），
 *     并且后台标签页不再强加 minIntervalMs 间隔；可见时照旧节流。
 *
 * v2.2.5
 *   · 修：列表页徽标「一次只出一条」，整页要几分钟才铺满，看起来像卡在「查询中…」。
 *     根因是 renderList 里逐行 await fillListBadge，把整页串行化了——憨憨 100 行
 *     只出 3~4 个徽标、且任何一条慢查询都会堵住后面所有行。
 *     现在先给每一行同步占位「查询中…」，再各自落地；并发上限与请求间隔
 *     仍由页内队列 queue 统一控制（默认 2 并发 / 250ms）。
 *
 * v2.2.4
 *   · 逐站适配剩余 5 站（2026-10-20 实测 DOM）：
 *     - 春天(springsunday)：每行两个 td.embedded，副标题在 div.torrent-smalldescr，
 *       标签是 span.torrent-tag（CMCTV/官方/中字），不剔会当成片名候选
 *     - 猫站(pterclub)：副标题混着促销倒计时「剩余时间：21时23分」，
 *       字幕标签是 a.chs_tag；现在按站点清洗
 *     - 我堡(ourbits)：td0 里带 [热门] 角标与「剩余时间：2月25天」
 *     - 观众(audiences)：结构与我堡类似，但有 IMDb 链接（覆盖率约 40%），走 IMDb 直查
 *     - 憨憨(hhanclub)：不是 table，是 div.torrents-table + div.torr* 布局；
 *       列表副标题在 div.torrent-info-text-small_name（后面接 | 全10集 | 类型 | 导演，
 *       截到第一个 | 之前）；详情页**没有 h1**，标题/副标题都在 div.grid 的
 *       「标签 + 值」格子里，靠 gridCellByLabel('标题'/'副标题') 取
 *   · 新增 cleanSubtitle()：统一清掉促销倒计时、[热门]/[置顶] 角标
 *
 * v2.2.3
 *   · 列表页 / 详情页两级查询（配套插件 1.1.11）：
 *     - 列表页只查「在不在库里」（lite=1），不再为画质/季集/分集额外打媒体服务器；
 *       剧集因此不在列表页显示缺集，画质等信息只在详情页给。
 *     - 详情页先用列表页已经查到的结论铺上（peek 本地缓存，不重新请求、不重新识别），
 *       同时打一次 full 查询补全画质 / 季集 / 缺集，回来再替换。
 *     - 打开过详情页的行，缓存里已有全量结论，列表页直接显示（含缺集）。
 *
 * v2.2.2
 *   · 多标签页并发治理（配套插件 1.1.10）：
 *     - 详情页 / 手动操作走 priority=high，列表页默认排队；页面内的请求队列
 *       也分成 hot / normal 两级，详情页那条不用排在 100 条列表后面。
 *     - 插件侧闸门排队超时（busy=true / timeout=true）不再当「未入库」，
 *       改显示橙色「排队中」，并按 busyRetryMs 自动重试（默认 4s、8s，共 2 次）；
 *       这类结果不写缓存，避免这一行把「排队中」记成结论。
 *
 * v2.2.1
 *   · 适配听听歌(TTG, totheglory.im)：它和 NexusPHP 通用结构不一样——
 *     种子 id 在 <tr id="838510"> 上，标题链接是 /t/838510/ 而不是 details.php
 *     （details.php 的链接全是 filelist/做种/下载/评论计数），标题与中文名
 *     同在一个 <b> 里用 <br> 分隔。现在按站点分派采集器，徽标插在 <b> 之后，
 *     不会被加粗。IMDb 仍取 .name_right 里的 imdb_rate 链接。
 *
 * v2.2.0
 *   · 只为**指定的几个站**打标，其它站点一律不跑：
 *       天空(hdsky.me) 彩虹岛/岛(ptchdbits.co) 春天(springsunday.net)
 *       猫站(pterclub.net/.com) 我堡(ourbits.club) 观众(audiences.me)
 *       憨憨(hhanclub.net/.top) 听听歌/TTG(totheglory.im)
 *   · 这几个站的**域名从 MP 站点表实时取**（插件 /sites 接口，按站点名匹配），
 *     站点换域名（如 hhanclub.top → .net）只需在 MP 里改，脚本不用动；
 *     拉不到时退回脚本内置的同名域名清单。
 *   · 适配彩虹岛(CHDBits, ptchdbits.co)：副标题里的标签胶囊（.tag/官方/独占/
 *     DIY/国语/中字/特效）、热度角标(.hot)、编码圈(.circle) 会被剔除，
 *     避免「官方」「独占」被当成片名候选。
 *
 * v2.1.2
 *   · 列表页恢复「双向」状态：已入库标绿色「库内」，未入库标灰色「未入库」
 *     （缺集仍是「库内·缺 SxxExx」）。查询中/查询失败仍不占位，避免闪烁与误导。
 *
 * v2.1.1
 *   · 安全整改：脚本里不再内置任何 MP 域名与 API Key。地址/密钥改由油猴菜单
 *     「① 设置 MP 地址与 API Key」录入，只存在 Tampermonkey 存储里，
 *     所以本文件可以直接分享、发 GreasyFork、进仓库。
 *   · 地址为空时不插样式、不发请求，只在控制台提示一次。
 *
 * v2.1.0
 *   · 识别优先用站点已填的 IMDb：行内 icon-imdb 图标链接（如
 *     <a href="http://www.imdb.com/title/tt14205554"><img src="/icon-imdb.png"></a>）
 *     里的 tt 号直接作为标识传给插件，省掉片名解析与模糊比对；
 *     站点没填 IMDb 时才退回「主标题 + 副标题」识别。
 *
 * v2.0.1 适配修正：
 *   · 列表项按种子 ID 归组识别，评论数 / 做种数 / 下载数等纯数字链接不再被当成种子；
 *   · 列表徽标只在「已入库」时显示（缺集显示「库内·缺 SxxExx」），
 *     未入库 / 查询中 / 查询失败都不渲染，避免刷花站点原生列表；
 *   · 分辨率、DoVi、音轨、字幕等详细信息只在详情页面板展示。
 *
 * 依赖：MP 已安装并启用 PtLibraryCheck 插件（plugins.v3/ptlibrarycheck）。
 *       接口：GET {mp.host}/api/v1/plugin/PtLibraryCheck/check?title=..&subtitle=..&priority=..&lite=..
 *            GET {mp.host}/api/v1/plugin/PtLibraryCheck/sites
 *            GET {mp.host}/api/v1/plugin/PtLibraryCheck/stats（并发闸门状态，排查用）
 * =====================================================================
 */

(function () {
  'use strict';

  /* =====================================================================
   * 一、配置区
   * ===================================================================== */
  const CONFIG = {
    // MoviePilot 地址（不带结尾斜杠）。
    // ⚠️ 本仓库不内置任何具体地址：首次使用请用油猴菜单
    //    「① 设置 MP 地址与 API Key」录入，只存在你自己的 Tampermonkey 里。
    //    也可以直接把地址写在这里（自己用可以，发布前请清空）。
    mp: {
      host: '',
      // MP API Key：留空则用匿名接口（需插件开启「允许匿名访问」）。
      // 用油猴菜单「① 设置 MP 地址与 API Key」录入，存放在 Tampermonkey 存储里，
      // 不会出现在脚本文件中 —— 发布到 GreasyFork 前无需清理任何密钥。
      apiKey: '',
    },
    // 插件接口路径（改了插件类名才需要动这里）
    apiPath: '/api/v1/plugin/PtLibraryCheck/check',
    // 站点清单接口：以 MP 站点表为准，站点换域名不用改本脚本
    apiSitesPath: '/api/v1/plugin/PtLibraryCheck/sites',

    // 站点过滤：只有「适配名单」里的站才打标
    //   1) ADAPTED_SITE_NAMES 是 MP 里的站点名，脚本据此从 MP 站点表取域名；
    //   2) CONFIG.sites.extra 可临时加域名（比如站点换域名但 MP 还没改）；
    //   3) CONFIG.sites.exclude 优先级最高，可把名单里的站临时关掉。
    sites: {
      extra: [],       // 例：['pt.example.com']
      exclude: [],     // 例：['hdsky.me']（临时不打标）
    },

    // 行为
    behavior: {
      listPage: true,        // 列表页打标
      detailPage: true,      // 详情页展示
      autoDetectSite: false, // 未在内置名单的域名，是否靠页面特征自动识别（学校站请保持 false）
      episodeCheck: true,    // 剧集是否查具体分集（关掉只显示「剧集在库」）
      cacheHours: 24,        // 查询缓存时长
      minIntervalMs: 250,    // 请求最小间隔（只打自己的 MP，无站点风控压力）
      concurrency: 2,        // 并发数
      retries: 2,            // 网络错误/超时/5xx 的重试次数（0 = 不重试）
      retryDelayMs: 400,     // 首次重试等待，之后按次数线性递增
      badgeCompact: true,    // 列表页用紧凑徽标
      // 插件侧并发闸门排队超时（同时开了很多标签页）时的自动重试：
      // 第一次等 busyRetryMs，第二次等 2 倍，最多 busyRetries 次
      busyRetries: 2,
      busyRetryMs: 4000,
    },
  };

  // 适配名单：MP 里的站点名 → 从 MP 站点表取出这些站的域名。
  // 站点改域名时 MP 里一改就生效，脚本不用重装。
  const ADAPTED_SITE_NAMES = [
    '天空',             // HDSKY（已适配，作参照）
    '彩虹岛',           // CHDBits / 岛
    '春天',             // Spring Sunday
    '猫站',             // PterClub
    '我堡',             // OurBits
    '观众',             // Audiences
    '憨憨',             // HHanClub
    '听听歌',           // TTG / ToTheGlory
  ];

  // 内置域名（与 ADAPTED_SITE_NAMES 对应），MP 不可达时的兜底
  const BUILTIN_SITES = [
    'hdsky.me',
    'ptchdbits.co',
    'springsunday.net',
    'pterclub.net', 'pterclub.com',
    'ourbits.club',
    'audiences.me',
    'hhanclub.net', 'hhanclub.top',
    'totheglory.im',
  ];

  // 从 MP 站点表里筛出来的适配站域名（loadMpSites 填充）；为空时只用 BUILTIN_SITES
  let MP_SITES = [];

  /* =====================================================================
   * 二、基础设施
   * ===================================================================== */
  const TAG = '[PT媒体库检测]';
  const log = (...a) => console.log(TAG, ...a);

  /** MP 地址：优先取油猴存储里的 mp_host，其次 CONFIG.mp.host（默认空，不内置任何真实地址） */
  function mpHost() {
    let h = '';
    try {
      const v = GM_getValue('mp_host');
      if (v) h = String(v).trim();
    } catch (e) {}
    if (!h) h = String(CONFIG.mp.host || '').trim();
    return h.replace(/\/+$/, '');
  }

  function mpKey() {
    try {
      const v = GM_getValue('mp_api_key');
      if (v) return String(v).trim();
    } catch (e) {}
    return (CONFIG.mp.apiKey || '').trim();
  }

  function cacheGet(key, maxAgeMs) {
    try {
      const raw = GM_getValue('q_' + key);
      if (!raw) return null;
      const obj = JSON.parse(raw);
      if (Date.now() - obj.t > maxAgeMs) return null;
      return obj.v;
    } catch (e) { return null; }
  }
  function cacheSet(key, val) {
    try { GM_setValue('q_' + key, JSON.stringify({ t: Date.now(), v: val })); } catch (e) {}
  }
  /** 客户端缓存的「口径版本」。
   *  插件侧的判定逻辑一改（比如收紧「库内」门槛），这里的缓存就必须失效，
   *  否则页面会一直显示上一版的结论 —— 实测 1.1.7 改完后页面还是旧数字。
   *  默认跟随脚本 @version：每次发版自动作废旧缓存。 */
  const SCRIPT_VER = (() => {
    try {
      if (typeof GM_info !== 'undefined' && GM_info.script && GM_info.script.version) {
        return String(GM_info.script.version);
      }
    } catch (e) {}
    return '2.3.1';
  })();

  /** 不受「后台标签页定时器节流」影响的下一轮调度。
   *  坑（2026-10-20 实测）：Chrome 对隐藏标签页的 setTimeout 有节流——后台
   *  5 分钟后降到约 1 次/分钟。列表页 100 行靠 setTimeout 排队时，会表现为
   *  「只落地 3~4 条、其余永远查询中」，而 MP 侧闸门是空闲的（请求根本没发出）。
   *  MessageChannel 的 message 事件属于普通任务队列，不参与定时器节流，
   *  所以这里用它替代 setTimeout 做 0 延迟调度。 */
  const tick = (() => {
    // Node 单测里不建通道：未关闭的 MessagePort 会吊住事件循环，测试进程退不出去
    if (typeof MessageChannel === 'undefined' || typeof document === 'undefined') {
      return (fn) => setTimeout(fn, 0);
    }
    const ch = new MessageChannel();
    const pending = [];
    ch.port1.onmessage = () => {
      const fn = pending.shift();
      if (fn) fn();
    };
    return (fn) => { pending.push(fn); ch.port2.postMessage(0); };
  })();

  // 限流队列：全局串行 + 最小间隔 + 两级优先级
  //   hot  详情页 / 手动刷新 —— 你正在看的那条，先出队
  //   tasks 列表页批量 —— 慢慢跑，不抢详情页的位
  const queue = {
    running: 0,
    last: 0,
    tasks: [],       // normal
    hot: [],         // high
    push(fn, priority) {
      const isHot = String(priority || '').toLowerCase() === 'high';
      return new Promise((resolve, reject) => {
        (isHot ? this.hot : this.tasks).push({ fn, resolve, reject });
        this._next();
      });
    },
    _next() {
      while (this.running < CONFIG.behavior.concurrency && (this.hot.length || this.tasks.length)) {
        const task = this.hot.length ? this.hot.shift() : this.tasks.shift();
        this.running++;
        // 后台标签页不强加请求间隔：反正用户看不到，而 setTimeout 的等待会被
        // 节流成 1 次/分钟（正是列表页卡死的原因）。并发上限仍由 concurrency 管。
        const hidden = (typeof document !== 'undefined') && document.hidden === true;
        const gap = hidden ? 0 : CONFIG.behavior.minIntervalMs - (Date.now() - this.last);
        const run = async () => {
          try {
            const r = await task.fn();
            task.resolve(r);
          } catch (e) { task.reject(e); }
          finally {
            this.running--;
            this.last = Date.now();
            this._next();
          }
        };
        if (gap > 0) setTimeout(run, gap); else tick(run);
      }
    },
  };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function hash(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) | 0; }
    return (h >>> 0).toString(36);
  }

  /* =====================================================================
   * 三、MP 插件接口
   * ===================================================================== */

  /** 单次请求：只发一次，失败时给 Error 打上 retryable 标记（4xx/业务失败不重试） */
  function mpRequest(url, headers) {
    return new Promise((resolve, reject) => {
      const retryable = (msg) => {
        const e = new Error(msg);
        e.retryable = true;
        return e;
      };
      GM_xmlhttpRequest({
        method: 'GET',
        url,
        headers,
        timeout: 30000,
        onload: (res) => {
          if (res.status === 401 || res.status === 403) {
            reject(new Error('MP 接口需要密钥（油猴菜单 → 填写 MP API Key，或在插件里开允许匿名访问）'));
            return;
          }
          if (res.status < 200 || res.status >= 300) {
            const e = new Error(`HTTP ${res.status} ${url.slice(0, 80)}`);
            e.retryable = res.status >= 500;   // 5xx 是服务端抖动，值得重试
            reject(e);
            return;
          }
          let d;
          try {
            d = JSON.parse(res.responseText);
          } catch (e) {
            // 反代 30s 断流时会返回状态 200 + HTML 错误页（实测猫站偶发一条）。
            // 这不是「未入库」，当可重试处理。
            const head = String(res.responseText || '').replace(/^\uFEFF?\s*/, '').slice(0, 16).toLowerCase();
            if (head.indexOf('<!doctype') === 0 || head.indexOf('<html') === 0) {
              reject(retryable('网关返回 HTML 错误页（多为 30s 超时），稍后重试'));
            } else {
              reject(new Error('响应不是 JSON'));
            }
            return;
          }
          if (d && d.success === false) { reject(new Error(d.message || '插件返回失败')); return; }
          resolve(d && d.data ? d.data : d);
        },
        onerror: () => reject(retryable('network error ' + url.slice(0, 80))),
        ontimeout: () => reject(retryable('timeout ' + url.slice(0, 80))),
      });
    });
  }

  /**
   * 调 PtLibraryCheck 插件。
   * 100 条列表并发查询时，反代偶发掐连接（network error），单次失败不代表没入库，
   * 所以网络错误 / 超时 / 5xx 会自动重试 2 次（共 3 次），4xx 与业务失败不重试。
   * @param {string} rawTitle 标题行 + 换行 + 副标题
   * @param {string} [imdb]  站点已填的 IMDb ID（tt1234567），有就走 IMDb 直查
   * @param {string} [priority] 'high' 走插件侧的插队（详情页/手动刷新），默认排队
   * @param {boolean} [lite]   true = 浅查询：只要「在不在库里」，不要画质/季集
   * @returns {Promise<{found, type, title, year, quality, seasons, episode, server, reason}>}
   */
  function checkLibrary(rawTitle, imdb, priority, lite) {
    const isHot = String(priority || '').toLowerCase() === 'high';
    return queue.push(async () => {
      const host = mpHost();
      if (!host) {
        throw new Error('未设置 MoviePilot 地址：油猴菜单 → ① 设置 MP 地址与 API Key');
      }
      const nl = rawTitle.indexOf('\n');
      const title = (nl >= 0 ? rawTitle.slice(0, nl) : rawTitle).trim();
      const subtitle = nl >= 0 ? rawTitle.slice(nl + 1).trim() : '';
      const url = `${host}${CONFIG.apiPath}`
        + `?title=${encodeURIComponent(title)}`
        + (subtitle ? `&subtitle=${encodeURIComponent(subtitle)}` : '')
        + (imdb ? `&imdb=${encodeURIComponent(imdb)}` : '')
        + (isHot ? '&priority=high' : '')
        + (lite ? '&lite=1' : '');
      const headers = { Accept: 'application/json' };
      const key = mpKey();
      if (key) headers['X-API-KEY'] = key;

      let lastErr = null;
      const tries = 1 + Math.max(0, Math.floor(CONFIG.behavior.retries || 0));
      const delay = Math.max(0, CONFIG.behavior.retryDelayMs || 0);
      for (let attempt = 0; attempt < tries; attempt++) {
        try {
          return await mpRequest(url, headers);
        } catch (e) {
          lastErr = e;
          if (e.retryable !== true) break;
          if (attempt < tries - 1 && delay) {
            await new Promise((r) => setTimeout(r, delay * (attempt + 1)));
          }
        }
      }
      throw lastErr || new Error('查询失败');
    }, priority);
  }

  const checkCache = new Map();      // key -> Promise（进行中或已完成的请求）
  const resultCache = new Map();     // key -> 已拿到的结果（同步可读，详情页首屏要用）
  /** 排队超时的结果不是结论，不能写缓存 —— 否则这一行会「记住」未入库 */
  function isBusy(r) {
    return !!(r && (r.busy === true || r.timeout === true));
  }

  /** 缓存键：full / lite 分开（lite 少画质与季集，不能当 full 用） */
  function cacheKeysOf(rawTitle, imdb) {
    const base = `mp|${imdb || ''}|${rawTitle}`;
    return {
      full: base + '|full',
      lite: base + '|lite',
      storeFull: 'chk_' + hash(SCRIPT_VER + '|' + base + '|full'),
      storeLite: 'chk_' + hash(SCRIPT_VER + '|' + base + '|lite'),
    };
  }

  /** 同步读已有结论（不发请求）——详情页首屏先用列表页那条结论铺上 */
  function peekCached(rawTitle, imdb) {
    const k = cacheKeysOf(rawTitle, imdb);
    const ttl = CONFIG.behavior.cacheHours * 3600e3;
    const pick = (v) => (v && !isBusy(v) && !v.error ? v : null);
    // 内存里已拿到的结论（本次会话刚查过，最快）
    const mem = pick(resultCache.get(k.full)) || pick(resultCache.get(k.lite));
    if (mem) return mem;
    // 再翻油猴存储（上一个页面 / 列表页那趟写的）
    return pick(cacheGet(k.storeFull, ttl)) || pick(cacheGet(k.storeLite, ttl));
  }

  /**
   * @param {boolean} [lite] true = 列表页的浅查询（只要在不在库里）
   *
   * 缓存优先级：lite 请求可以先吃 full 的结论（full 是超集）；
   * full 请求绝不能吃 lite 的结论（会缺画质/分集），否则详情页面板永远补不全。
   */
  async function checkTorrentCached(rawTitle, imdb, priority, lite) {
    const k = cacheKeysOf(rawTitle, imdb);
    // 有 IMDb 时以 IMDb 为主键：同一部片子不同压制版本可共用一次查询结果
    const key = lite ? k.lite : k.full;
    const mem = checkCache.get(key) || (lite ? checkCache.get(k.full) : null);
    if (mem) {
      const val = await mem;                      // 可能是 Promise，也可能已是结果
      if (!isBusy(val)) { resultCache.set(key, val); return val; }
      checkCache.delete(key);                     // 上次是排队超时，这次重查
    }
    const ttl = CONFIG.behavior.cacheHours * 3600e3;
    if (lite) {
      const full = cacheGet(k.storeFull, ttl);    // 打开过详情页的行，列表直接显示完整结论
      if (full && !isBusy(full)) {
        checkCache.set(key, Promise.resolve(full));
        resultCache.set(key, full);
        return full;
      }
    }
    const storeKey = lite ? k.storeLite : k.storeFull;
    const hit = cacheGet(storeKey, ttl);
    if (hit && !isBusy(hit)) {
      checkCache.set(key, Promise.resolve(hit));
      resultCache.set(key, hit);
      return hit;
    }
    const p = checkLibrary(rawTitle, imdb, priority, lite).then((r) => {
      if (!isBusy(r)) {
        cacheSet(storeKey, r);
        resultCache.set(key, r);
        // full 的结论比 lite 全，顺手把 lite 那份也升级掉，列表就不用再显示浅结论
        if (!lite && r && r.found) {
          cacheSet(k.storeLite, r);
          if (resultCache.has(k.lite)) resultCache.set(k.lite, r);
        }
      }
      return r;
    })
      .catch((e) => ({ found: false, error: e.message }));
    checkCache.set(key, p);
    return p;
  }

  /* =====================================================================
   * 四、站点适配（NexusPHP 通用）
   * ===================================================================== */
  /** 域名后缀匹配：host 等于 d，或是以 .d 结尾（子域也算） */
  function hostMatch(host, domain) {
    const h = String(host || '').toLowerCase();
    const d = String(domain || '').toLowerCase().trim().replace(/^\*?\.?/, '');
    if (!h || !d) return false;
    return h === d || h.endsWith('.' + d);
  }

  const SITES_TTL_MS = 12 * 3600e3;
  let sitesPromise = null;

  /** 从 MP 站点表取「适配名单」里那些站的域名（12 小时缓存，模块内也只请求一次）。
   *  站点改名（hhanclub.top → .net）只需在 MP 里改，脚本不用重装。
   *  只收 ADAPTED_SITE_NAMES 里的站，别的站一律不认。
   *  失败不抛异常，静默退回 BUILTIN_SITES。 */
  function loadMpSites(force) {
    if (sitesPromise && !force) return sitesPromise;
    sitesPromise = (async () => {
      const cacheKey = 'sites_' + hash(SCRIPT_VER + '|sites2');
      if (!force) {
        const hit = cacheGet(cacheKey, SITES_TTL_MS);
        if (Array.isArray(hit) && hit.length) { MP_SITES = hit; return MP_SITES; }
      }
      const host = mpHost();
      if (!host) return MP_SITES;
      try {
        const headers = { Accept: 'application/json' };
        const key = mpKey();
        if (key) headers['X-API-KEY'] = key;
        const data = await mpRequest(`${host}${CONFIG.apiSitesPath}`, headers);
        const list = [];
        const found = [];
        ((data && data.sites) || []).forEach((s) => {
          const name = String((s && s.name) || '').trim();
          if (ADAPTED_SITE_NAMES.indexOf(name) < 0) return;   // 不在适配名单，跳过
          if (found.indexOf(name) < 0) found.push(name);
          (s.hosts || []).forEach((h) => {
            const v = String(h || '').toLowerCase().trim();
            if (v && list.indexOf(v) < 0) list.push(v);
          });
        });
        if (list.length) {
          MP_SITES = list;
          cacheSet(cacheKey, list);
        }
        log(`MP 站点：适配 ${found.length}/${ADAPTED_SITE_NAMES.length} 个（${found.join('、')}），`
          + `域名 ${list.join(' ')}`);
      } catch (e) {
        log('拉取 MP 站点清单失败，退回内置名单：' + e.message);
      }
      return MP_SITES;
    })();
    return sitesPromise;
  }

  function siteExcluded() {
    return CONFIG.sites.exclude.some((d) => hostMatch(location.hostname, d));
  }

  function siteAllowed() {
    const host = location.hostname;
    if (siteExcluded()) return false;
    if (CONFIG.sites.extra.some((d) => hostMatch(host, d))) return true;
    // 适配名单里的站：域名优先用 MP 站点表的（站点改名不用改脚本），拉不到用内置
    if (MP_SITES.some((d) => hostMatch(host, d))) return true;
    if (BUILTIN_SITES.some((d) => hostMatch(host, d))) return true;
    // 未在内置名单：默认不跑，避免误伤学校/无关站点；打开 autoDetectSite 后按页面特征识别
    if (CONFIG.behavior.autoDetectSite && document.querySelector('a[href*="details.php?id="]') != null) return true;
    return false;
  }

  function isListPage() {
    return /torrents\.php|browse\.php|index\.php|movie\.php|music\.php/i.test(location.pathname)
      || document.querySelector('table.torrents, table#torrenttable') != null;
  }
  function isDetailPage() {
    if (/details\.php/i.test(location.pathname) || /details\.php\?/i.test(location.search)) return true;
    // TTG（totheglory）：详情页是 /t/<种子ID>/，列表页才是 /browse.php
    // —— 只认 details.php 的话 TTG 详情页一个字都不会渲染。
    // 限定主机，免得别站恰好也有 /t/123/ 这种路径被误判成详情页
    if (/\btotheglory\.im$/i.test(location.hostname)
      && /^\/t\/\d+(?:\/|$)/.test(location.pathname)) return true;
    return false;
  }

  /* ---- 列表页种子行识别 ------------------------------------------------
   * 坑：NexusPHP 一行里的「评论数 / 做种 / 下载 / 完成」全是
   *     details.php?id=<同一个ID> 的链接，只是链接文本是纯数字；而且标题
   *     常放在嵌套 table（td.torrentname > table > td.embedded > b > a）里，
   *     所以「锚点 → 最近的 tr」会得到内层 tr，而计数链接得到外层 tr，
   *     按 tr 去重完全拦不住 → 数字旁边长出一堆徽标。
   * 解法：
   *   1) 按 URL 里的种子 ID 归组（同一 ID 只算一个种子，天然去重）；
   *   2) 纯数字 / 计数类文本直接判为噪声（评论数、做种数、下载数）；
   *   3) 每个 ID 只保留「最像标题」的锚点（在 <b>/<strong> 里、文本最长、
   *      且不是评论页/做种列表这类附属视图）。
   * ------------------------------------------------------------------- */
  /** 从锚点取「种子 ID」；只认 details.php，绝不吃 userdetails.php 这类子串命中 */
  function torrentIdOf(a) {
    const href = (a && a.getAttribute && a.getAttribute('href')) || '';
    const path = href.split('?')[0].split('#')[0];
    const file = path.slice(path.lastIndexOf('/') + 1).toLowerCase();
    if (file !== 'details.php') return '';
    const m = href.match(/[?&]id=(\d+)/i);
    return m ? m[1] : '';
  }

  /** 「7」「(12)」「评论 7」「做种 32」这类计数文本 → 不是种子标题 */
  function isCounterText(txt) {
    const s = String(txt || '').replace(/[\s,，]/g, '');
    if (!s) return true;
    if (/^\d+$/.test(s)) return true;                        // 7 / 32
    if (/^[（(]\d+[)）]$/.test(s)) return true;               // (7)
    if (/^(评论|评论数|回復|回复|做种|做種|下载|下載|完成|收藏|感谢|感謝|查看)/.test(s)) return true;
    return false;
  }

  /** 该链接是否指向种子的附属视图（评论页 / 做种列表 / 下载列表） */
  function isSubViewLink(a) {
    const href = (a && a.getAttribute && a.getAttribute('href')) || '';
    return /(cmtpage|#startcomments|#seeders|#leechers|#thankers|#viewfilelist)/i.test(href);
  }

  function anchorScore(a) {
    const txt = (a.textContent || '').replace(/\s+/g, ' ').trim();
    let score = txt.length;
    if (a.closest('b, strong')) score += 1000;   // 标题一般加粗
    if (isSubViewLink(a)) score -= 5000;         // 附属视图一律不优先
    return score;
  }

  /** 行内 IMDb：站点种子行里的 icon-imdb 图标链接，href 里带 tt 号
   *    <a href="http://www.imdb.com/title/tt14205554"><img src="/pic/icon-imdb.png"> 7.4</a>
   *  站点填了就用它做识别（比解析标题快，也不会认错同名作品）。
   */
  function imdbOfRow(row) {
    if (!row) return '';
    const pick = (s) => {
      const m = String(s || '').match(/tt\d{6,10}/i);
      return m ? m[0].toLowerCase() : '';
    };
    const link = row.querySelector('a[href*="imdb.com/title/tt"]');
    if (link) {
      const v = pick(link.getAttribute('href'));
      if (v) return v;
    }
    const img = row.querySelector('img[src*="icon-imdb"], img[title*="imdb"], img[alt*="imdb"]');
    if (img) {
      const a = img.closest('a');
      const v = pick((a && a.getAttribute('href')) || img.getAttribute('title') || img.getAttribute('alt'));
      if (v) return v;
    }
    // 兜底：行内任意位置出现 tt 号（部分站点把 ID 写成纯文本）
    return pick(row.textContent);
  }

  /** 列表页：找出所有种子行 [{id, row, link, anchor, title, subtitle, imdb}]
   *  按站点结构分派：NexusPHP 通用（标题链接是 details.php）与 TTG（标题链接是 /t/<id>/）。 */
  function collectListItems() {
    const host = location.hostname;
    if (hostMatch(host, 'totheglory.im')) return collectTtgItems();
    return collectNexusItems();
  }

  /* ---------------------------------------------------------------------
   * 各站副标题位置（2026-10-20 实测，逐站抓的 DOM）
   *   · 春天(sunday)  每行两个 td.embedded，标题在 div.torrent-title，
   *                   副标题在 div.torrent-smalldescr，标签是 span.torrent-tag
   *   · 憨憨(hhanclub) 新版前端不是 table，是 div.torrents-table + div.torr*；
   *                   副标题在 div.torrent-info-text-small_name，
   *                   后面还接着「| 全10集 | 4K 杜比视界 | 类型: … | 导演: …」
   *   其余站（HDSKY/CHD/观众/我堡/猫站）用整格文本再清洗即可
   * ------------------------------------------------------------------- */
  const SUBTITLE_SPECS = [
    ['springsunday.net', '.torrent-smalldescr', false],
    ['hhanclub.net', '.torrent-info-text-small_name', true],   // true = 只取第一个 | 之前
  ];

  /** 取副标题前要剔掉的装饰元素：站点标签胶囊、促销图标、置顶角标、自己的徽标 */
  const DECOR_SELECTOR = 'a, script, style, .ptmc-badge, .ptmc-holder, .optiontag, img, '
    + '.tag, [class^="tag-"], .circle, .hot, .torrent-tag, .chs_tag, .sticky, '
    + '.pro_free, .pro_2x, [class*="pro_"]';

  /** 副标题清洗：促销倒计时、热门角标这些不是片名，留着只会干扰识别 */
  function cleanSubtitle(raw) {
    let t = String(raw || '');
    t = t.replace(/剩余时间\s*[:：]\s*\S+/g, ' ');
    t = t.replace(/(^|\s)剩余\s*[:：]\s*\S+/g, ' ');
    t = t.replace(/[\[【]\s*(热门|置顶|推荐|HOT|Hot|hot)\s*[\]】]/g, ' ');
    t = t.replace(/\s+/g, ' ').trim();
    return t.slice(0, 300);
  }

  function subtitleSpecOf(host) {
    for (let i = 0; i < SUBTITLE_SPECS.length; i++) {
      const [domain, sel, cutPipe] = SUBTITLE_SPECS[i];
      if (hostMatch(host, domain)) return { sel, cutPipe };
    }
    return null;
  }

  /** NexusPHP 通用（HDSKY / 彩虹岛 / 春天 / 猫舍 / 观众 / 憨憨 …） */
  function collectNexusItems() {
    const best = new Map();   // torrentId -> { a, title, score }
    document.querySelectorAll('a[href*="details.php"]').forEach((a) => {
      const id = torrentIdOf(a);
      if (!id) return;
      const title = (a.textContent || '').replace(/\s+/g, ' ').trim();
      if (title.length < 2 || isCounterText(title)) return;  // 评论数/做种数等噪声
      const score = anchorScore(a);
      const prev = best.get(id);
      if (!prev || score > prev.score) best.set(id, { a, title, score });
    });

    const out = [];
    best.forEach(({ a, title }, id) => {
      const row = a.closest('tr') || a.closest('div.torrent, li') || a.parentElement;
      if (!row) return;
      // 副标题：同单元格内链接之外的文本（日期/体积/类型/字幕等）
      // 顺便去掉本脚本自己插入的徽标，避免二次运行时把「库内」混进查询串
      let subtitle = '';
      // 1) 有专门容器的站，直接取那一块（比整格文本干净得多）
      const spec = subtitleSpecOf(location.hostname);
      if (spec) {
        const box = row.querySelector(spec.sel);
        if (box) {
          const clone = box.cloneNode(true);
          // 站点自己的装饰要在取副标题前剔掉，否则会跟中文片名粘成一片：
          //   · .optiontag  —— HDSKY 等的标签（Dolby Vision/国语/中字/HDR10）
          //   · .tag/.hot/.circle —— 彩虹岛(CHD)的标签胶囊（官方/独占/DIY/国语/中字/特效）、
          //                          热度角标、编码圆圈（h5）；不剔的话「官方」「独占」
          //                          会被插件当成片名候选，真正的片名反而排到后面
          //   · .torrent-tag —— 春天(SSD)的 CMCTV/官方/中字 胶囊
          //   · .chs_tag    —— 猫站(PterClub)的 中字/英字 标签
          clone.querySelectorAll(DECOR_SELECTOR).forEach((n) => n.remove());
          subtitle = clone.textContent.replace(/\s+/g, ' ').trim();
          if (spec.cutPipe) subtitle = subtitle.split('|')[0];
        }
      }
      // 2) 没配容器（或容器没命中）：退回整格文本
      if (!subtitle) {
        const cell = a.closest('td') || a.parentElement;
        if (cell) {
          const clone = cell.cloneNode(true);
          clone.querySelectorAll(DECOR_SELECTOR).forEach((n) => n.remove());
          subtitle = clone.textContent.replace(/\s+/g, ' ').trim();
        }
      }
      subtitle = cleanSubtitle(subtitle);
      out.push({ id, row, link: a, anchor: a, title, subtitle, imdb: imdbOfRow(row) });
    });
    return out;
  }

  /** 从元素里按 <br> 切成两段：前半是发布名，后半是中文名/字幕说明。
   *  TTG 的标题是 <a href="/t/838510/"><b>Passenger 2026 …-DIY@TTG <br>
   *  <span>鬼上车 / 乘吓(港) / 亡命旅人</span> *DIY 官译简繁粤中字</b></a>，
   *  中英文混在同一个 <b> 里，靠 <br> 分隔。 */
  function splitByBr(el) {
    let head = '';
    let tail = '';
    let afterBr = false;
    const walk = (node) => {
      if (node.nodeType === 3) {
        const t = String(node.nodeValue || '').replace(/\s+/g, ' ');
        if (afterBr) tail += t; else head += t;
        return;
      }
      if (node.nodeType !== 1) return;
      if (node.tagName === 'BR') { afterBr = true; return; }
      for (let n = node.firstChild; n; n = n.nextSibling) walk(n);
    };
    for (let n = el.firstChild; n; n = n.nextSibling) walk(n);
    return { head: head.trim(), tail: tail.trim() };
  }

  /** TTG（totheglory.im）：table#torrent_table，种子 id 在 <tr id="838510"> 上，
   *  标题链接是 /t/<id>/，IMDb 在 .name_right 的 imdb_rate 里。 */
  function collectTtgItems() {
    const out = [];
    const rows = document.querySelectorAll('#torrent_table tr[id]');
    rows.forEach((row) => {
      const id = String(row.id || '').trim();
      if (!/^\d+$/.test(id)) return;                       // 表头/分组行没有数字 id
      let link = null;
      row.querySelectorAll('a[href]').forEach((a) => {
        if (link) return;
        const href = a.getAttribute('href') || '';
        if (new RegExp('^/t/' + id + '/$|^/t/' + id + '$|\\.im/t/' + id + '/$').test(href)) link = a;
      });
      if (!link) {
        // 兜底：任意 /t/<数字>/ 且带 <b> 的链接（标题一定加粗）
        row.querySelectorAll('a[href*="/t/"]').forEach((a) => {
          if (link) return;
          if (/\/(\d+)\/?$/.test(a.getAttribute('href') || '') && a.querySelector('b')) link = a;
        });
      }
      if (!link) return;
      const box = link.querySelector('b') || link;
      const { head, tail } = splitByBr(box);
      const title = (head || (box.textContent || '')).replace(/\s+/g, ' ').trim();
      if (title.length < 2) return;
      out.push({
        id,
        row,
        link,
        // 徽标插在加粗标题之后（.name_left 内），别插进 <b> 里，免得继承加粗
        anchor: box,
        title,
        subtitle: tail.slice(0, 300),
        imdb: imdbOfRow(row),
      });
    });
    return out;
  }

  /** 详情页：标题 + 副标题 */
  /** 新版前端（憨憨 HHanClub 等）的「标签 + 值」信息格：
   *  页面里没有 h1，#top 也不存在，标题/副标题都是 div.grid 里成对的两格。
   *  label 用去空白、去尾冒号后的文本比较（站点有时写「标题：」）。 */
  function gridCellByLabel(label) {
    const nodes = document.querySelectorAll('.grid > div');
    for (let i = 0; i < nodes.length; i++) {
      const el = nodes[i];
      if (el.children.length > 1) continue;              // 只要叶子格子
      const t = (el.textContent || '').replace(/\s+/g, '').replace(/[:：]$/, '');
      if (t !== label) continue;
      const v = el.nextElementSibling;
      if (v) return v;
    }
    return null;
  }

  function collectDetailItem() {
    const h1 = document.querySelector('#top h1, h1#top, h1') || document.querySelector('#top');
    let title = h1 ? (h1.textContent || '').trim() : '';
    title = title.replace(/^\[.*?\]\s*/, '').trim();
    let subtitle = '';
    const sub = document.querySelector('#subtitle, .subtitle, font.subtitle');
    if (sub) subtitle = sub.textContent.replace(/\s+/g, ' ').trim();

    // 新版前端（憨憨）：标题 / 副标题 都在「标签 + 值」格子里，没有 h1
    let anchorEl = h1;
    if (!title) {
      const cell = gridCellByLabel('标题');
      if (cell) {
        title = (cell.textContent || '').replace(/\s+/g, ' ').trim();
        // 面板插在整个信息块后面，别插进格子里挤成两列
        anchorEl = cell.closest('.grid') || cell;
      }
    }
    if (!subtitle) {
      const cell = gridCellByLabel('副标题');
      if (cell) {
        // 「中文名 / 别名 | 全10集 | 4K 杜比视界 | 类型: … | 导演: …」→ 只留片名部分
        subtitle = (cell.textContent || '').replace(/\s+/g, ' ').trim().split('|')[0];
      }
    }
    if (!subtitle) {
      // NexusPHP：标题下方的简介/种子小标题表格
      const tbl = document.querySelector('#top + table, table.main + table, #descr + table');
      if (tbl) subtitle = tbl.textContent.replace(/\s+/g, ' ').trim().slice(0, 300);
    }
    // 最后的兜底：站点把片名写进了 document.title 的引号里
    if (!title) {
      const m = String(document.title || '').match(/["“]([^"”]{6,})["”]/);
      if (m) title = m[1].trim();
    }
    if (!title) return null;
    // 详情页同样优先用站点已填的 IMDb
    return {
      el: anchorEl || document.body,
      title,
      subtitle: cleanSubtitle(subtitle),
      imdb: imdbOfRow(document.body),
    };
  }

  /* =====================================================================
   * 五、UI
   * ===================================================================== */
  const STYLE = `
  .ptmc-badge{display:inline-flex;align-items:center;gap:4px;margin-left:6px;padding:1px 6px;
    border-radius:3px;font-size:11px;line-height:16px;vertical-align:middle;white-space:nowrap;
    font-family:system-ui,"Microsoft YaHei",sans-serif;cursor:default}
  .ptmc-badge.in{background:#e6f7e9;color:#1a7f37;border:1px solid #b7e4c1}
  .ptmc-badge.part{background:#fff8e1;color:#b25e09;border:1px solid #f5d9a8}
  .ptmc-badge.out{background:#f2f2f3;color:#8a8f98;border:1px solid #e2e4e7}
  .ptmc-badge.err{background:#fdecea;color:#c0392b;border:1px solid #f5c6c0}
  .ptmc-badge.load{background:#eef4ff;color:#4a6cf7;border:1px solid #d6e2ff}
  .ptmc-badge.wait{background:#fff4e5;color:#a35f00;border:1px solid #f3dbb5}
  .ptmc-panel{margin:8px 0 12px;padding:10px 12px;border:1px solid #d8dce3;border-radius:6px;
    background:#fafbfc;font-size:13px;line-height:1.7;font-family:system-ui,"Microsoft YaHei",sans-serif}
  .ptmc-panel .t{font-weight:600;margin-bottom:4px}
  .ptmc-panel .t.in{color:#1a7f37}.ptmc-panel .t.out{color:#8a8f98}.ptmc-panel .t.part{color:#b25e09}
  .ptmc-panel table{border-collapse:collapse;margin-top:4px}
  .ptmc-panel td{padding:2px 10px 2px 0;vertical-align:top}
  .ptmc-panel .k{color:#6b7280;white-space:nowrap}
  /* 详情页面板：grid 布局（憨憨等新版前端）插进站点自己的 .grid 里，横跨两列 */
  .ptmc-panel[data-layout="grid"]{grid-column:1/-1;display:grid;
    grid-template-columns:100px calc(100% - 100px - 1.25rem);gap:0 1.25rem;justify-items:start;
    align-items:start;width:100%;margin:8px 0;padding:8px 10px;
    border:1px solid #e5e7eb;border-radius:8px;background:#fafbfc}
  .ptmc-panel[data-layout="grid"] .ptmc-glabel{color:#6b7280;font-size:12px;line-height:1.7}
  .ptmc-panel[data-layout="grid"] .ptmc-gval{min-width:0}
  .ptmc-panel[data-layout="nexus"]{width:100%;margin:8px 0}
  .ptmc-panel[data-layout="nexus"] .ptmc-tag{margin-right:4px}
  .ptmc-rows{border-collapse:collapse}
  .ptmc-rows td{padding:1px 10px 1px 0;vertical-align:top}
  /* 播放按钮（Plex / Emby / Jellyfin） */
  .ptmc-plays{display:inline-flex;flex-wrap:wrap;gap:6px}
  .ptmc-play{display:inline-flex;align-items:center;gap:5px;padding:3px 9px 3px 6px;
    border:1px solid #d8dce3;border-radius:999px;background:#fff;color:#374151;
    font-size:12px;line-height:1;text-decoration:none;vertical-align:middle}
  .ptmc-play:hover{border-color:#9aa0a6;background:#f6f7f9;text-decoration:none}
  .ptmc-play svg{display:block;flex:none}
  .ptmc-tag{display:inline-block;margin-right:4px;padding:0 5px;border-radius:3px;background:#eef1f5;color:#374151;font-size:12px}
  .ptmc-tag.dv{background:#f3e8ff;color:#7c3aed;font-weight:600}
  .ptmc-tag.hdr{background:#e0f2fe;color:#0369a1}
  .ptmc-tag.res{background:#dcfce7;color:#15803d;font-weight:600}
  `;

  function injectStyle() {
    if (document.getElementById('ptmc-style')) return;
    const s = document.createElement('style');
    s.id = 'ptmc-style';
    s.textContent = STYLE;
    document.head.appendChild(s);
  }

  function tagsHtml(q) {
    if (!q) return '';
    const parts = [];
    if (q.resolution) parts.push(`<span class="ptmc-tag res">${esc(q.resolution)}</span>`);
    if (q.dv) parts.push(`<span class="ptmc-tag dv">杜比视界</span>`);
    else if (q.hdr && q.hdr !== 'SDR') parts.push(`<span class="ptmc-tag hdr">${esc(q.hdr)}</span>`);
    if (q.codec) parts.push(`<span class="ptmc-tag">${esc(q.codec)}</span>`);
    return parts.join('');
  }

  /** 列表页紧凑徽标 —— 每一行都必须有且只有一个状态，四选一：
   *    · 查询中…        灰色蓝字（还没结果，先占位，避免行内空白）
   *    · 库内           绿色
   *    · 库内·缺 SxxExx 橙色（剧集在库但缺这一集）
   *    · 未入库         灰色
   *  查询失败单独给红字「查询失败」（不是"未入库"，不能把错误当结论）。
   *  分辨率、DoVi、音轨等详细信息只在详情页面板里给（panelHtml）。
   */
  function listPendingHtml() {
    return '<span class="ptmc-badge load" title="正在查询 MoviePilot">查询中…</span>';
  }

  function listFailHtml(msg) {
    return `<span class="ptmc-badge err" title="${esc(msg || '查询失败')}">查询失败</span>`;
  }

  /** 插件侧并发闸门排队超时（多个标签页同时刷）——这不是「未入库」，
   *  给橙色「排队中」，稍后自动重试。 */
  function listBusyHtml(msg) {
    return `<span class="ptmc-badge wait" title="${esc(msg || '查询排队中，稍后自动重试')}">排队中</span>`;
  }

  function badgeHtml(r) {
    if (r && r.error) return listFailHtml(r.error);
    if (!r) return listPendingHtml();
    if (isBusy(r)) return listBusyHtml(r.reason);
    if (r.found) {
      const ep = r.episode;
      if (ep && ep.found === false) {
        const s = String(ep.season).padStart(2, '0');
        const e = String(ep.episode).padStart(2, '0');
        return `<span class="ptmc-badge part" title="${esc(r.reason || '剧集在库，但缺这一集')}">库内·缺 S${s}E${e}</span>`;
      }
      const src = r.quality && r.quality.src_name;
      return `<span class="ptmc-badge in" title="${esc(r.reason || src || '已在媒体库')}">库内</span>`;
    }
    // tooltip 只展示 MP 自己解析出来的片名 —— 排查「库里有却判未入库」时，
    // 需要看到 MP 到底把这条发布名认成了什么；解析不出来就如实说，不拿发布名充数
    const name = r.display || '';
    const recog = r.recognized && r.recognized.title;
    const tip = [
      name ? `MP 识别：${name}${r.year ? ' (' + r.year + ')' : ''}` : 'MP 未能解析出片名',
      recog && recog !== name ? `识别补充：${recog}` : '',
      r.reason || '媒体库中未找到该资源',
    ].filter(Boolean).join('｜');
    return `<span class="ptmc-badge out" title="${esc(tip)}">未入库</span>`;
  }

  /* ---- 详情页面板排版：跟着站点自己的布局走 -------------------------
   * 三类：
   *   nexus —— NexusPHP（天空/彩虹岛/春天/猫站/我堡/观众/TTG…）：
   *            页面主体是 table.mainouter，字段行是 td.rowhead / td.rowfollow，
   *            这里就用同样的 <table class="main"> + rowhead/rowfollow 渲染，
   *            看起来就是站点自己多出来的一块信息。
   *   grid  —— 憨憨(hhanclub) 这类新版前端：没有 table，主体是 div.grid 的
   *            「标签 + 值」两列（100px 标签列），面板就插进这个 grid，
   *            用 grid-column:1/-1 横跨两列，内部同样是「标签 + 值」。
   *   plain —— 认不出来时的兜底：自带边框的卡片（原来的样子）。
   * ---------------------------------------------------------------- */
  function detailLayout(anchor) {
    const el = anchor || null;
    // 用锚点自己的 document：单测里会切 document，写死全局 document 会判错
    const doc = (el && el.ownerDocument)
      || (typeof document !== 'undefined' ? document : null);
    if (!doc || !doc.querySelector) return 'plain';
    if (el && el.closest && el.closest('.grid')) return 'grid';
    // NexusPHP 有两代写法：老的是 table.mainouter > tr > td.outer，
    // 新的（春天 / 猫站）是 main.mainouter > div.outer；两代都用 rowhead/rowfollow
    if (doc.querySelector('.mainouter')) return 'nexus';
    if (doc.querySelector('.outer')) return 'nexus';
    return 'plain';
  }

  /** 把 [标签, 值] 行按站点布局拼成 HTML */
  function rowsToHtml(rows, lay, titleHtml) {
    if (lay === 'grid') {
      return `<div class="ptmc-glabel">${titleHtml}</div>`
        + `<div class="ptmc-gval"><table class="ptmc-rows">`
        + rows.map(([k, v]) => `<tr><td class="k">${k}</td><td>${v}</td></tr>`).join('')
        + '</table></div>';
    }
    if (lay === 'nexus') {
      return `<table class="main" border="0" cellspacing="0" cellpadding="5" width="100%">`
        + `<tr><td class="colhead" colspan="2">${titleHtml}</td></tr>`
        + rows.map(([k, v]) => `<tr><td class="rowhead" width="10%">${k}</td>`
          + `<td class="rowfollow" width="90%">${v}</td></tr>`).join('')
        + '</table>';
    }
    return `<div class="t ${''}">${titleHtml}</div><table>`
      + rows.map(([k, v]) => `<tr><td class="k">${k}</td><td>${v}</td></tr>`).join('')
      + '</table>';
  }

  /* ---- 媒体服务器播放按钮 ----
   * 库内才给按钮：Plex / Emby / Jellyfin 各按自己的类型显示官方配色图标，
   * 两台服务器都有（插件返回 sources）就两个都给。
   * 图标是内联 SVG：脚本不依赖任何外链图片，也不受站点 CSP 影响。 */
  const PLAY_META = {
    plex: { label: 'Plex', color: '#cc7b19' },
    emby: { label: 'Emby', color: '#52b54b' },
    jellyfin: { label: 'Jellyfin', color: '#aa5cc3' },
  };

  function playIconSvg(type) {
    if (type === 'plex') {
      // Plex 官方标记：竖条 + 上下两根横杠（形似 P）
      return '<svg viewBox="0 0 32 32" width="15" height="15" aria-hidden="true">'
        + '<rect width="32" height="32" rx="7" fill="#cc7b19"/>'
        + '<g fill="#fff"><rect x="8.5" y="6" width="4.4" height="20" rx="2.2"/>'
        + '<rect x="8.5" y="6" width="14.5" height="5" rx="2.5"/>'
        + '<rect x="8.5" y="21" width="14.5" height="5" rx="2.5"/></g></svg>';
    }
    if (type === 'emby') {
      // Emby 官方标记：白色圆环 + 一道横杠（小写 e 的意象）
      return '<svg viewBox="0 0 32 32" width="15" height="15" aria-hidden="true">'
        + '<rect width="32" height="32" rx="7" fill="#52b54b"/>'
        + '<path fill="#fff" d="M16 6.5a9.5 9.5 0 1 0 0 19 9.5 9.5 0 0 0 0-19zm0 3.6'
        + 'a5.9 5.9 0 1 1 0 11.8 5.9 5.9 0 0 1 0-11.8z"/>'
        + '<path fill="#52b54b" d="M6.5 15.1h14.2v1.8H6.5z"/></svg>';
    }
    if (type === 'jellyfin') {
      // Jellyfin 官方标记：双色方块对拼
      return '<svg viewBox="0 0 32 32" width="15" height="15" aria-hidden="true">'
        + '<rect width="32" height="32" rx="7" fill="#aa5cc3"/>'
        + '<path fill="#fff" d="M7 7h8.4v8.4H7zM16.6 7H25v8.4h-8.4z'
        + 'M7 16.6h8.4V25H7zM16.6 16.6H25V25h-8.4z"/></svg>';
    }
    return '<svg viewBox="0 0 32 32" width="15" height="15" aria-hidden="true">'
      + '<rect width="32" height="32" rx="7" fill="#4a6cf7"/>'
      + '<path fill="#fff" d="M12 9l12 7-12 7z"/></svg>';
  }

  function playButtonHtml(type, url, server) {
    if (!url) return '';
    const meta = PLAY_META[type] || { label: server || '播放', color: '#4a6cf7' };
    const tip = `在 ${server || meta.label} 中打开${server ? '（' + meta.label + '）' : ''}`;
    return `<a class="ptmc-play" href="${esc(url)}" target="_blank" rel="noopener noreferrer"`
      + ` title="${esc(tip)}">${playIconSvg(type)}<span>${esc(meta.label)}</span></a>`;
  }

  /** 播放按钮组：主命中 + 其它服务器上也有（sources）的都给 */
  function playButtonsHtml(r) {
    if (!r || !r.found) return '';
    const out = [];
    out.push(playButtonHtml(r.server_type || '', r.play_url, r.server || ''));
    (r.sources || []).forEach((s) => {
      out.push(playButtonHtml(s.server_type || '', s.play_url, s.server || ''));
    });
    const html = out.filter(Boolean).join('');
    return html || '';
  }

  /** 详情页面板
   *  @param {boolean} [partial] 只用列表页已有结论铺的首屏，还在补全细节
   *  @param {string} [layout]  强制用某种布局（单测用；页面里自动按站点判定） */
  function panelHtml(r, rawTitle, partial, layout) {
    const lay = layout || detailLayout(
      (typeof document !== 'undefined' && document.getElementById)
        ? (document.getElementById('ptmc-detail') || document.querySelector('h1') || null)
        : null);
    if (r && r.error) {
      return `<div class="ptmc-panel" data-layout="${lay}">`
        + `<div class="t">媒体库查询失败：${esc(r.error)}</div></div>`;
    }
    if (!r) {
      return `<div class="ptmc-panel" data-layout="${lay}"><div class="t">媒体库查询中…</div></div>`;
    }
    if (isBusy(r)) {
      return `<div class="ptmc-panel" data-layout="${lay}"><div class="t">媒体库查询排队中…</div>`
        + `<div style="color:#6b7280;font-size:12px">${esc(r.reason || '同时查询的页面太多，已排队等待')}，稍后自动重试。</div></div>`;
    }
    const head = (rawTitle || '').split('\n')[0];
    if (!r.found) {
      // MP 解析结果如实展示（诊断用）；解析不出来就说解析不出来，不用发布名顶替
      const parsed = r.display
        ? `${esc(r.display)}${r.year ? ' (' + esc(r.year) + ')' : ''}`
          + `<span style="color:#9aa0a6">（${esc(r.display_src || 'parsed')}）</span>`
        : '<span style="color:#c0392b">未能解析出片名</span>';
      const rows2 = [
        ['MP 识别', parsed],
      ];
      if (r.recognized && r.recognized.title) {
        rows2.push(['识别补充', `${esc(r.recognized.title)}${r.recognized.original_title && r.recognized.original_title !== r.recognized.title ? ' / ' + esc(r.recognized.original_title) : ''}${r.recognized.tmdb_id ? ` <span style="color:#9aa0a6">TMDB ${esc(r.recognized.tmdb_id)}</span>` : ''}`]);
      }
      rows2.push(['未匹配原因', esc(r.reason || '无匹配')]);
      rows2.push(['发布名', `<span style="color:#9aa0a6">${esc(head)}</span>`]);
      return `<div class="ptmc-panel" data-layout="${lay}">`
        + rowsToHtml(rows2, lay, '✗ 媒体库中未找到该资源') + '</div>';
    }
    const q = r.quality || {};
    const rows = [];
    const state = (r.episode && r.episode.found === false) ? 'part' : 'in';
    const stateText = (r.episode && r.episode.found === false)
      ? `<span style="color:#b25e09;font-weight:600">✓ 剧集在库，但缺这一集</span>`
      : `<span style="color:#1a7f35;font-weight:600">✓ 已在媒体库</span>`;
    rows.push(['状态', `${stateText}${r.reason ? ' <span style="color:#b25e09">（' + esc(r.reason) + '）</span>' : ''}`]);
    if (r.imdb) {
      const how = r.matched_by === 'imdb' ? '（站点 IMDb 直查）' : '（站点 IMDb）';
      rows.push(['IMDb', `<a href="https://www.imdb.com/title/${esc(r.imdb)}/" target="_blank" rel="noopener noreferrer">${esc(r.imdb)}</a><span style="color:#6b7280">${how}</span>`]);
    }
    if (r.server) rows.push(['媒体服务器', esc(r.server)]);
    if (r.title) rows.push(['库内标题', `${esc(r.title)}${r.year ? ' (' + esc(r.year) + ')' : ''}`]);
    if (q.src_name) rows.push(['库内版本', esc(q.src_name)]);
    const qs = [];
    if (q.resolution) qs.push(q.resolution);
    if (q.dv) qs.push('杜比视界 DoVi');
    else if (q.hdr && q.hdr !== 'SDR') qs.push(q.hdr);
    if (q.codec) qs.push(String(q.codec).toUpperCase());
    if (qs.length) rows.push(['画质', qs.map((x) => `<span class="ptmc-tag">${esc(x)}</span>`).join('')]);
    if (q.audio && q.audio.length) rows.push(['音轨', q.audio.map((x) => `<span class="ptmc-tag">${esc(x)}</span>`).join('')]);
    if (q.subtitles && q.subtitles.length) rows.push(['字幕', q.subtitles.map((x) => `<span class="ptmc-tag">${esc(x)}</span>`).join('')]);
    if (r.seasons && Object.keys(r.seasons).length) {
      const txt = Object.keys(r.seasons).sort((a, b) => Number(a) - Number(b))
        .map((s) => {
          const shown = r.seasons[s].length;
          // seasons 列表被插件截断到 20 集，episode_count 是该季真实总数
          const more = r.episode_count > shown ? `${shown}+/${r.episode_count}` : `${shown}`;
          return `S${String(s).padStart(2, '0')}(${more}集)`;
        }).join(' ');
      rows.push(['在库季集', `<span style="color:#374151">${esc(txt)}</span>`]);
    }
    if (q.path) rows.push(['路径', `<span style="color:#6b7280;word-break:break-all">${esc(q.path)}</span>`]);
    // 库内才给播放按钮：两台服务器都有（插件返回 sources）就两个都显示
    const plays = playButtonsHtml(r);
    if (plays) rows.push(['在库播放', `<span class="ptmc-plays">${plays}</span>`]);
    if (r.in_servers && r.in_servers.length > 1) {
      rows.push(['同时在', `<span style="color:#6b7280">${esc(r.in_servers.join('、'))}</span>`]);
    }
    const hint = partial
      ? '<div style="color:#6b7280;font-size:12px;margin:2px 0 4px">'
        + '列表页已确认在库，正在补全画质 / 季集…</div>'
      : '';
    return `<div class="ptmc-panel" data-layout="${lay}">${hint}`
      + rowsToHtml(rows, lay, `媒体库检测${state === 'part' ? '（缺集）' : ''}`) + '</div>';
  }

  /* =====================================================================
   * 六、渲染入口
   * ===================================================================== */
  const processed = new WeakSet();

  function renderList() {
    if (!CONFIG.behavior.listPage) return;
    const items = collectListItems();
    for (const it of items) {
      if (processed.has(it.link)) continue;
      processed.add(it.link);
      // 先占位「查询中…」：每一行都必须有状态，不能等结果才出现
      const holder = document.createElement('span');
      holder.className = 'ptmc-holder';
      holder.innerHTML = listPendingHtml();
      // anchor 是「徽标插在哪之后」：通用站就是标题链接；TTG 是标题所在的 <b>，
      // 免得徽标被塞进 <b> 里继承加粗
      (it.anchor || it.link).insertAdjacentElement('afterend', holder);
      // 标题行 + 副标题合并送检：片名取标题行，分辨率/季集等质量标签全文匹配
      const raw = it.subtitle ? `${it.title}\n${it.subtitle}` : it.title;
      // 千万别 await：整页串行会变成「一次只查一条」——一页 100 行要几分钟才铺满，
      // 而且任意一条慢查询会把后面所有行的「查询中…」占位都堵住（憨憨 100 行
      // 只出 3~4 个徽标就是这个原因）。并发上限和请求间隔统一交给页内队列 queue。
      fillListBadge(holder, raw, it.imdb, 0).catch(() => {});
    }
  }

  /** 列表行徽标填充：排队超时不算结论，隔一会儿自动重试（最多 busyRetries 次） */
  async function fillListBadge(holder, raw, imdb, attempt) {
    if (!holder.isConnected) return;               // 页面已刷新/行已移除
    // 列表页只要「在不在库里」：lite 浅查询不会为画质/季集多打媒体服务器
    const r = await checkTorrentCached(raw, imdb, 'normal', true);
    if (r && r.error) {
      warnOnce(r.error);
      holder.innerHTML = listFailHtml(r.error);
      return;
    }
    if (isBusy(r)) {
      holder.innerHTML = listBusyHtml(r.reason);
      const max = Math.max(0, Number(CONFIG.behavior.busyRetries) || 0);
      if (attempt < max) {
        const wait = Math.max(1000, Number(CONFIG.behavior.busyRetryMs) || 4000);
        setTimeout(() => { fillListBadge(holder, raw, imdb, attempt + 1); }, wait * (attempt + 1));
      }
      return;
    }
    holder.innerHTML = badgeHtml(r);
  }

  // 列表页查询失败只在控制台提示一次（页面上不显示错误徽标）
  let warned = false;
  function warnOnce(msg) {
    if (warned) return;
    warned = true;
    log('列表页查询失败（页面不显示）：' + msg);
  }

  async function renderDetail() {
    if (!CONFIG.behavior.detailPage) return;
    const d = collectDetailItem();
    if (!d || processed.has(d.el)) return;
    processed.add(d.el);
    const host = document.createElement('div');
    host.id = 'ptmc-detail';
    // 憨憨这类「标签 + 值」网格布局：面板要进到 grid 里才能和站点对齐，
    // 插在 grid 外面会变成独立一块，跟页面其它字段不对齐
    const lay = detailLayout(d.el);
    if (lay === 'grid' && d.el && d.el.classList && d.el.classList.contains('grid')) {
      d.el.appendChild(host);
    } else {
      d.el.insertAdjacentElement('afterend', host);
    }
    const raw = d.subtitle ? `${d.title}\n${d.subtitle}` : d.title;
    // 列表页那趟已经查过就先用它的结论铺上（不重新问、不重新识别），
    // 然后才去补画质 / 季集 / 分集这些「打开了才需要」的信息
    const known = peekCached(raw, d.imdb);
    host.innerHTML = known ? panelHtml(known, raw, true) : panelHtml(null, raw);
    // 详情页是你正在看的那条：走 high 优先级，插件侧插队、页内也插队
    await fillDetailPanel(host, raw, d.imdb, 0);
  }

  async function fillDetailPanel(host, raw, imdb, attempt) {
    if (!host.isConnected) return;
    const r = await checkTorrentCached(raw, imdb, 'high');
    host.innerHTML = panelHtml(r, raw);
    if (isBusy(r)) {
      const max = Math.max(0, Number(CONFIG.behavior.busyRetries) || 0);
      if (attempt < max) {
        const wait = Math.max(1000, Number(CONFIG.behavior.busyRetryMs) || 4000);
        setTimeout(() => { fillDetailPanel(host, raw, imdb, attempt + 1); }, wait * (attempt + 1));
      }
    }
  }

  async function run() {
    if (!mpHost()) {
      log('未设置 MoviePilot 地址：油猴菜单 →「① 设置 MP 地址与 API Key」');
      return;
    }
    if (siteExcluded()) return;          // 例外名单直接不跑，连清单都不拉
    await loadMpSites();                 // 站点清单以 MP 为准（失败退回内置名单）
    if (!siteAllowed()) return;
    injectStyle();
    if (isDetailPage()) await renderDetail();
    if (CONFIG.behavior.listPage && isListPage()) await renderList();
  }

  /* =====================================================================
   * 七、菜单
   * ===================================================================== */
  function menuSetup() {
    if (typeof GM_registerMenuCommand !== 'function') return;
    GM_registerMenuCommand('① 设置 MP 地址与 API Key', () => {
      const h = prompt('MoviePilot 地址（含 http/https 与端口，不带结尾斜杠）\n\n'
        + '例：https://mp.example.com:3000\n'
        + '只保存在你的 Tampermonkey 存储里，不会写进脚本文件。', mpHost());
      if (h == null) return;
      const host = h.trim().replace(/\/+$/, '');
      if (host) GM_setValue('mp_host', host); else GM_deleteValue('mp_host');
      const v = prompt('MoviePilot API Key\n\n'
        + '插件开启「允许匿名访问」时可留空。', mpKey());
      if (v != null) {
        if (v.trim()) GM_setValue('mp_api_key', v.trim()); else GM_deleteValue('mp_api_key');
      }
      location.reload();
    });
    GM_registerMenuCommand('② 清除检测缓存', () => {
      try {
        const keys = (GM_listValues ? GM_listValues() : []).filter((k) => k.startsWith('q_'));
        keys.forEach((k) => GM_deleteValue(k));
      } catch (e) {}
      location.reload();
    });
    GM_registerMenuCommand('③ 测试连通性', () => {
      const host = mpHost();
      if (!host) { alert('先设置 MP 地址：油猴菜单 → ① 设置 MP 地址与 API Key'); return; }
      const url = `${host}${CONFIG.apiPath}?title=test&priority=high`;
      GM_xmlhttpRequest({
        method: 'GET', url,
        headers: Object.assign({ Accept: 'application/json' }, mpKey() ? { 'X-API-KEY': mpKey() } : {}),
        timeout: 15000,
        onload: (res) => alert(`MP 接口 HTTP ${res.status}\n${res.responseText.slice(0, 300)}`),
        onerror: () => alert('网络错误：' + url),
        ontimeout: () => alert('超时：' + url),
      });
    });
    GM_registerMenuCommand('④ 重新拉取适配站点域名', async () => {
      try {
        const keys = (GM_listValues ? GM_listValues() : []).filter((k) => /^q_sites_/.test(k));
        keys.forEach((k) => GM_deleteValue(k));
      } catch (e) {}
      sitesPromise = null;
      const list = await loadMpSites(true);
      const cur = location.hostname;
      const state = siteExcluded() ? '在排除名单里，不适用'
        : (siteAllowed() ? '适用' : '不适用');
      alert(`适配站点域名（${list.length} 个）：\n${list.join('\n') || '（没取到，用内置名单）'}\n\n`
        + `当前站点 ${cur}：${state}`);
    });
  }

  /* =====================================================================
   * 八、启动（仅在浏览器 / 油猴环境执行，Node 下跳过以便单测 require）
   * ===================================================================== */
  if (typeof document !== 'undefined' && typeof GM_xmlhttpRequest !== 'undefined') {
    menuSetup();
    run();

    // AJAX / SPA 翻页后重跑（节流）
    let debounce = null;
    const observer = new MutationObserver(() => {
      if (debounce) return;
      debounce = setTimeout(() => { debounce = null; run(); }, 800);
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  // 排查用句柄：控制台里可以直接看队列/缓存状态（__ptmc.queue.running 等）。
  // 只暴露对象引用，不改变任何行为，出问题时不至于只能靠猜。
  try {
    // 沙箱里 window 不是页面 window，必须挂到 unsafeWindow 才能在控制台/调试里看到
    const dbgHost = (typeof unsafeWindow !== 'undefined') ? unsafeWindow : window;
    dbgHost.__ptmc = {
      config: CONFIG, queue, checkCache, resultCache,
      collectListItems, collectDetailItem, peekCached, cacheKeysOf,
    };
  } catch (e) {}

  // 供 Node 单测导出
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      CONFIG, BUILTIN_SITES, ADAPTED_SITE_NAMES, hash, badgeHtml, listPendingHtml, listFailHtml,
      listBusyHtml, isBusy, panelHtml, tagsHtml, queue,
      siteAllowed, isListPage, isDetailPage, collectListItems, collectDetailItem,
      collectNexusItems, collectTtgItems, splitByBr, cleanSubtitle, subtitleSpecOf,
      gridCellByLabel, detailLayout, rowsToHtml, playButtonsHtml, playButtonHtml,
      playIconSvg,
      torrentIdOf, isCounterText, isSubViewLink, anchorScore, imdbOfRow,
      checkLibrary, checkTorrentCached, cacheKeysOf, peekCached, mpKey, renderList, renderDetail,
      hostMatch, loadMpSites, siteExcluded,
      getMpSites: () => MP_SITES, setMpSites: (v) => { MP_SITES = v || []; },
    };
  }
})();
