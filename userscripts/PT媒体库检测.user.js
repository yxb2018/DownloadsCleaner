// ==UserScript==
// @name         PT 媒体库检测（MoviePilot 插件版）
// @namespace    https://local.ptmediacheck
// @version      2.1.4
// @description  在 PT 站点种子列表/详情页标注媒体库是否已有该资源；优先用站点已填的 IMDb ID 识别（最准最快），没有才解析标题+副标题。列表页标「库内 / 未入库」两种状态，详细信息在详情页展示。查询全部走 MoviePilot 的 PtLibraryCheck 插件，油猴本身不保存 Emby/Plex 密钥。
// @author       YaoXiaoBin
// @match        *://*.ourbits.club/*
// @match        *://*.pterclub.com/*
// @match        *://*.hdarea.club/*
// @match        *://*.mteam.cc/*
// @match        *://*.hdsky.me/*
// @match        *://*.chdbits.cid/*
// @match        *://*.pthome.net/*
// @match        *://*.soulvoice.club/*
// @match        *://*.hdhome.org/*
// @match        *://*.totheglory.im/*
// @match        *://*.springsunday.net/*
// @match        *://*.nicept.net/*
// @match        *://*.hhanclub.top/*
// @match        *://*.52pt.site/*
// @match        *://*.audiences.me/*
// @match        *://*.hdatmos.club/*
// @match        *://*.hdfans.org/*
// @match        *://*.ubits.club/*
// @match        *://*.pttime.org/*
// @match        *://*.pt.keepfrds.com/*
// @connect      *
// 说明：MP 地址由用户在油猴菜单里自行填写，脚本不内置任何具体域名，
//      因此这里只能用通配 @connect；如你不放心，可改成你自己的 MP 域名。
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_deleteValue
// @grant        GM_listValues
// @grant        GM_registerMenuCommand
// @run-at       document-idle
// ==/UserScript==

/*
 * =====================================================================
 * PT 媒体库检测 · MoviePilot 插件版（v2.1.2）
 * ---------------------------------------------------------------------
 * 架构：本脚本只负责「读页面 DOM + 调 MP 插件接口 + 渲染徽标/面板」。
 *      媒体库存在性、画质、分集判断全部由 MP 的 PtLibraryCheck 插件完成，
 *      凭据用的是 MP 里已配置的媒体服务器，所以：
 *        · 本脚本不需要 Emby / Plex 的任何密钥；
 *        · 只有 MP 插件开启「允许匿名访问」时，才连 MP API Key 都不用填。
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
 *     <a href="http://www.imdb.com/title/tt14205554"><img src="/pic/icon-imdb.png"></a>）
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
 *       接口：GET {mp.host}/api/v1/plugin/PtLibraryCheck/check?title=..&subtitle=..
 *
 * 旧版「直连 Emby/Plex」脚本保留为 PT媒体库检测-直连版.user.js，可作兜底。
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

    // 站点过滤：extra 里填要额外启用的域名；exclude 里填要排除的（学校等优先于 extra）
    sites: {
      extra: [],       // 例：['pt.example.com']
      exclude: [],     // 例：['byr.pt']（学校站）
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
    },
  };

  // 内置站点名单（NexusPHP 系，不含学校站）。用户在 CONFIG.sites.extra/exclude 增删
  const BUILTIN_SITES = [
    'ourbits.club', 'pterclub.com', 'hdarea.club', 'mteam.cc', 'hdsky.me',
    'chdbits.cid', 'pthome.net', 'soulvoice.club', 'hdhome.org',
    'totheglory.im', 'springsunday.net', 'nicept.net', 'hhanclub.top',
    '52pt.site', 'audiences.me', 'hdatmos.club', 'hdfans.org', 'ubits.club',
    'pttime.org', 'keepfrds.com',
  ];

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
    return '2.1.4';
  })();

  // 限流队列：全局串行 + 最小间隔
  const queue = {
    running: 0,
    last: 0,
    tasks: [],
    push(fn) {
      return new Promise((resolve, reject) => {
        this.tasks.push({ fn, resolve, reject });
        this._next();
      });
    },
    _next() {
      while (this.running < CONFIG.behavior.concurrency && this.tasks.length) {
        const task = this.tasks.shift();
        this.running++;
        const gap = CONFIG.behavior.minIntervalMs - (Date.now() - this.last);
        setTimeout(async () => {
          try {
            const r = await task.fn();
            task.resolve(r);
          } catch (e) { task.reject(e); }
          finally {
            this.running--;
            this.last = Date.now();
            this._next();
          }
        }, Math.max(gap, 0));
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
          try { d = JSON.parse(res.responseText); } catch (e) { reject(new Error('响应不是 JSON')); return; }
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
   * @returns {Promise<{found, type, title, year, quality, seasons, episode, server, reason}>}
   */
  function checkLibrary(rawTitle, imdb) {
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
        + (imdb ? `&imdb=${encodeURIComponent(imdb)}` : '');
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
    });
  }

  const checkCache = new Map();
  async function checkTorrentCached(rawTitle, imdb) {
    // 有 IMDb 时以 IMDb 为主键：同一部片子不同压制版本可共用一次查询结果
    const key = `mp|${imdb || ''}|${rawTitle}`;
    if (checkCache.has(key)) return checkCache.get(key);
    const hit = cacheGet('chk_' + hash(SCRIPT_VER + '|' + key), CONFIG.behavior.cacheHours * 3600e3);
    if (hit) { checkCache.set(key, hit); return hit; }
    const p = checkLibrary(rawTitle, imdb).then((r) => {
      cacheSet('chk_' + hash(SCRIPT_VER + '|' + key), r);
      return r;
    })
      .catch((e) => ({ found: false, error: e.message }));
    checkCache.set(key, p);
    return p;
  }

  /* =====================================================================
   * 四、站点适配（NexusPHP 通用）
   * ===================================================================== */
  function siteAllowed() {
    const host = location.hostname;
    if (CONFIG.sites.exclude.some((d) => host.includes(d))) return false;
    if (CONFIG.sites.extra.some((d) => host.includes(d))) return true;
    if (BUILTIN_SITES.some((d) => host.includes(d))) return true;
    // 未在内置名单：默认不跑，避免误伤学校/无关站点；打开 autoDetectSite 后按页面特征识别
    if (CONFIG.behavior.autoDetectSite && document.querySelector('a[href*="details.php?id="]') != null) return true;
    return false;
  }

  function isListPage() {
    return /torrents\.php|browse\.php|index\.php|movie\.php|music\.php/i.test(location.pathname)
      || document.querySelector('table.torrents, table#torrenttable') != null;
  }
  function isDetailPage() {
    return /details\.php/i.test(location.pathname) || /details\.php\?/i.test(location.search);
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

  /** 列表页：找出所有种子行 [{id, row, link, title, subtitle, imdb}] */
  function collectListItems() {
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
      const cell = a.closest('td') || a.parentElement;
      if (cell) {
        const clone = cell.cloneNode(true);
        // .optiontag 是站点自己的标签（Dolby Vision/国语/中字/HDR10），
        // 留在副标题里会跟中文片名粘成一片（「中字HDR10倩女幽魂2：人间道」），
        // 干扰插件侧从副标题里抽中文片名
        clone.querySelectorAll('a, script, style, .ptmc-badge, .ptmc-holder, .optiontag, img')
          .forEach((n) => n.remove());
        subtitle = clone.textContent.replace(/\s+/g, ' ').trim().slice(0, 300);
      }
      out.push({ id, row, link: a, title, subtitle, imdb: imdbOfRow(row) });
    });
    return out;
  }

  /** 详情页：标题 + 副标题 */
  function collectDetailItem() {
    const h1 = document.querySelector('#top h1, h1#top, h1') || document.querySelector('#top');
    let title = h1 ? (h1.textContent || '').trim() : '';
    title = title.replace(/^\[.*?\]\s*/, '').trim();
    let subtitle = '';
    const sub = document.querySelector('#subtitle, .subtitle, font.subtitle');
    if (sub) subtitle = sub.textContent.replace(/\s+/g, ' ').trim();
    if (!subtitle) {
      // NexusPHP：标题下方的简介/种子小标题表格
      const tbl = document.querySelector('#top + table, table.main + table, #descr + table');
      if (tbl) subtitle = tbl.textContent.replace(/\s+/g, ' ').trim().slice(0, 300);
    }
    if (!title) return null;
    // 详情页同样优先用站点已填的 IMDb
    return { el: h1 || document.body, title, subtitle, imdb: imdbOfRow(document.body) };
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
  .ptmc-panel{margin:8px 0 12px;padding:10px 12px;border:1px solid #d8dce3;border-radius:6px;
    background:#fafbfc;font-size:13px;line-height:1.7;font-family:system-ui,"Microsoft YaHei",sans-serif}
  .ptmc-panel .t{font-weight:600;margin-bottom:4px}
  .ptmc-panel .t.in{color:#1a7f37}.ptmc-panel .t.out{color:#8a8f98}.ptmc-panel .t.part{color:#b25e09}
  .ptmc-panel table{border-collapse:collapse;margin-top:4px}
  .ptmc-panel td{padding:2px 10px 2px 0;vertical-align:top}
  .ptmc-panel .k{color:#6b7280;white-space:nowrap}
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

  function badgeHtml(r) {
    if (r && r.error) return listFailHtml(r.error);
    if (!r) return listPendingHtml();
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

  /** 详情页面板 */
  function panelHtml(r, rawTitle) {
    if (r && r.error) return `<div class="ptmc-panel"><div class="t">媒体库查询失败：${esc(r.error)}</div></div>`;
    if (!r) return `<div class="ptmc-panel"><div class="t">媒体库查询中…</div></div>`;
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
      return `<div class="ptmc-panel"><div class="t out">✗ 媒体库中未找到该资源</div>
        <table>${rows2.map(([k, v]) => `<tr><td class="k">${k}</td><td>${v}</td></tr>`).join('')}</table>
        <div style="color:#9aa0a6;font-size:12px;margin-top:4px">发布名：${esc(head)}</div></div>`;
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
    return `<div class="ptmc-panel"><div class="t ${state}">媒体库检测</div><table>${rows.map(([k, v]) => `<tr><td class="k">${k}</td><td>${v}</td></tr>`).join('')}</table></div>`;
  }

  /* =====================================================================
   * 六、渲染入口
   * ===================================================================== */
  const processed = new WeakSet();

  async function renderList() {
    if (!CONFIG.behavior.listPage) return;
    const items = collectListItems();
    for (const it of items) {
      if (processed.has(it.link)) continue;
      processed.add(it.link);
      // 先占位「查询中…」：每一行都必须有状态，不能等结果才出现
      const holder = document.createElement('span');
      holder.className = 'ptmc-holder';
      holder.innerHTML = listPendingHtml();
      it.link.insertAdjacentElement('afterend', holder);
      // 标题行 + 副标题合并送检：片名取标题行，分辨率/季集等质量标签全文匹配
      const raw = it.subtitle ? `${it.title}\n${it.subtitle}` : it.title;
      const r = await checkTorrentCached(raw, it.imdb);
      if (r && r.error) {
        warnOnce(r.error);
        holder.innerHTML = listFailHtml(r.error);
        continue;
      }
      holder.innerHTML = badgeHtml(r);
    }
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
    d.el.insertAdjacentElement('afterend', host);
    host.innerHTML = panelHtml(null, d.title);
    const raw = d.subtitle ? `${d.title}\n${d.subtitle}` : d.title;
    const r = await checkTorrentCached(raw, d.imdb);
    host.innerHTML = panelHtml(r, raw);
  }

  async function run() {
    if (!siteAllowed()) return;
    if (!mpHost()) {
      log('未设置 MoviePilot 地址：油猴菜单 →「① 设置 MP 地址与 API Key」');
      return;
    }
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
      const url = `${host}${CONFIG.apiPath}?title=test`;
      GM_xmlhttpRequest({
        method: 'GET', url,
        headers: Object.assign({ Accept: 'application/json' }, mpKey() ? { 'X-API-KEY': mpKey() } : {}),
        timeout: 15000,
        onload: (res) => alert(`MP 接口 HTTP ${res.status}\n${res.responseText.slice(0, 300)}`),
        onerror: () => alert('网络错误：' + url),
        ontimeout: () => alert('超时：' + url),
      });
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

  // 供 Node 单测导出
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      CONFIG, BUILTIN_SITES, hash, badgeHtml, listPendingHtml, listFailHtml,
      panelHtml, tagsHtml,
      siteAllowed, isListPage, isDetailPage, collectListItems, collectDetailItem,
      torrentIdOf, isCounterText, isSubViewLink, anchorScore, imdbOfRow,
      checkLibrary, checkTorrentCached, mpKey, renderList, renderDetail,
    };
  }
})();
