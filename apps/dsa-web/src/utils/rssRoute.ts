/** RSSHub route param helpers — mirror the backend Express-style param parsing. */

export interface RouteParam {
  name: string;
  optional: boolean;
  hasRegex: boolean;
}

export interface RouteParamOption {
  value: string;
  label: string;
}

const PARAM_RE = /:([a-zA-Z_][a-zA-Z0-9_]*)(\{[^}]+\})?(\?)?/g;

/** Parse ``:name`` / ``:name?`` / ``:name{regex}?`` params from a route path. */
export function parseRouteParams(routePath: string): RouteParam[] {
  const params: RouteParam[] = [];
  let m: RegExpExecArray | null;
  PARAM_RE.lastIndex = 0;
  while ((m = PARAM_RE.exec(routePath)) !== null) {
    params.push({
      name: m[1],
      hasRegex: Boolean(m[2]),
      optional: m[3] === '?',
    });
  }
  return params;
}

/**
 * Extract concrete param values from a route's `example` field (e.g.
 * example `/xueqiu/stock_info/SZ000002` for path `/xueqiu/stock_info/:id/:type?`
 * → { id: 'SZ000002' }).
 */
export function paramsFromExample(routePath: string, example: string): Record<string, string> {
  const params = parseRouteParams(routePath);
  if (!params.length || !example) return {};
  // Split both into segments and align positionally.
  const tmplSegs = routePath.split('/').filter(Boolean);
  const exSegs = example.split('/').filter(Boolean);
  const out: Record<string, string> = {};
  let exIdx = 0;
  for (const seg of tmplSegs) {
    const pm = seg.match(/^:([a-zA-Z_][a-zA-Z0-9_]*)(\{[^}]+\})?(\?)?$/);
    if (pm) {
      const val = exSegs[exIdx];
      if (val !== undefined) out[pm[1]] = val;
      exIdx += 1;
    } else {
      // Literal segment — advance example pointer only if it matches.
      if (exSegs[exIdx] === seg) exIdx += 1;
    }
  }
  return out;
}

/** Parse a two-column Markdown table such as RSSHub's `Name | ID` route docs. */
export function parseMarkdownParamOptions(markdown: string): RouteParamOption[] {
  if (!markdown || !markdown.includes('|')) return [];
  const rows: string[][] = [];
  for (const rawLine of markdown.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line.startsWith('|') || !line.endsWith('|')) continue;
    const cells = line.slice(1, -1).split('|').map((cell) => cell.trim());
    if (cells.length < 2) continue;
    if (cells.every((cell) => /^[-: ]+$/.test(cell))) continue;
    rows.push(cells);
  }
  if (rows.length < 1) return [];

  // Single-row header table: one `|` row of labels with no data row beneath
  // (RSSHub writes it as a bare value list, e.g. realtimenews tag:
  // `| 全部 | 重要 | A 股 | ... |` followed by only a separator). Each cell is
  // both the value and the label — there is no separate ID column.
  if (rows.length === 1 && rows[0].length > 2) {
    const seen = new Set<string>();
    return rows[0].flatMap((cell): RouteParamOption[] => {
      const value = cell.trim();
      if (!value || seen.has(value)) return [];
      seen.add(value);
      return [{ value, label: value }];
    });
  }

  if (rows.length < 2) return [];

  // Horizontal table: labels in the first row, values in the second row.
  if (rows[0].length > 2 && rows[1].length === rows[0].length) {
    return rows[1].flatMap((value, index): RouteParamOption[] => {
      const label = rows[0][index];
      return label && value ? [{ value, label: `${label}（${value}）` }] : [];
    });
  }

  // Vertical table: `Name | ID` header followed by one option per row.
  const options: RouteParamOption[] = [];
  const seen = new Set<string>();
  for (const [label, value] of rows) {
    if (!label || !value) continue;
    if (/^(name|名称|label)$/i.test(label) && /^(id|value|值)$/i.test(value)) continue;
    if (seen.has(value)) continue;
    seen.add(value);
    options.push({ value, label: `${label}（${value}）` });
  }
  return options;
}

// Words that appear in backticks inside a param description but are NOT enum
// values (JS API names, prose mentions, etc.). Prevents them from polluting the
// option list when we infer options from a plain-string description.
const NON_ENUM_BACKTICK_WORDS = new Set([
  'encodeURIComponent', 'encodeURI', 'decodeURI', 'decodeURIComponent',
  'true', 'false', 'null', 'undefined',
]);

/**
 * Infer enum options from a plain-string param description whose allowed values
 * are listed in backticks (e.g. section: "`macrodatas` 或 `report`，默认为
 * `macrodatas`"). RSSHub ships many params as a bare string instead of a typed
 * `{options}` object, so the form would otherwise render a bare input with only
 * a grey hint — users guess and hit upstream "route is empty" 503s.
 *
 * Conservative on purpose: only ASCII identifier-like tokens (≥2 chars, or a
 * short digit run) are treated as values, which excludes the Chinese labels
 * some descriptions also wrap in backticks (e.g. jisilu `主题`/`回复`) and
 * stray single letters (xueqiu fund `F`). Known non-enum backtick words are
 * filtered. Returns [] when nothing safe is found so the bare input + hint
 * path is preserved.
 */
export function parseEnumFromDescription(description: string): RouteParamOption[] {
  if (!description) return [];
  const ticks = description.match(/`([^`]+)`/g) || [];
  const seen = new Set<string>();
  const out: RouteParamOption[] = [];
  for (const raw of ticks) {
    const value = raw.slice(1, -1).trim();
    if (!value || seen.has(value)) continue;
    // ASCII identifier-like, or a short numeric token. Reject Chinese, single
    // letters, and whitespace/slash-only fragments.
    const isIdentLike = /^[A-Za-z][A-Za-z0-9_-]{1,}$/.test(value);
    const isNumeric = /^[0-9]{1,4}$/.test(value);
    if (!isIdentLike && !isNumeric) continue;
    if (NON_ENUM_BACKTICK_WORDS.has(value)) continue;
    seen.add(value);
    out.push({ value, label: value });
  }
  return out;
}

/**
 * Resolve a path param's RSSHub metadata entry by name — with a fallback for the
 * common key mismatch where RSSHub names the metadata key differently from the
 * path segment. e.g. `/cls/subject/:id?` documents its sole param under
 * `parameters.category` (not `parameters.id`); `/21caijing/channel/:name?` uses
 * `category` for `:name`. ~47 single-param routes ship this way, so a strict
 * name lookup leaves their input with no hint and no prefilled default.
 *
 * Resolution order:
 *   1. exact name match (`parameters[name]`);
 *   2. if there is exactly one path param and exactly one metadata entry, use
 *      that entry regardless of its key (the 1:1 mismatch case);
 *   3. otherwise nothing.
 * Multi-param routes with reordered/different keys are deliberately NOT guessed
 * — positional mapping there is unreliable (see cursor/blog, greasyfork, oreno3d).
 */
export function resolveParamMetadata(
  name: string,
  parameters: Record<string, unknown> | undefined,
  paramCount: number,
): unknown {
  if (!parameters) return undefined;
  const exact = parameters[name];
  if (exact !== undefined) return exact;
  const keys = Object.keys(parameters);
  if (paramCount === 1 && keys.length === 1) return parameters[keys[0]];
  return undefined;
}

/**
 * Extract a default value declared in a plain-string param description, e.g.
 * `分类，默认为 \`Industry/Comment\`，即行业评论` → `Industry/Comment`. Unlike
 * parseEnumFromDescription this accepts arbitrary path-like tokens (slashes,
 * digits, mixed case) because the default is a single concrete value, not a
 * value from a constrained set — e.g. chinaratings `category` defaults to
 * `Industry/Comment`, 81rc `category` to `sy/gzdt_210283`. Used to prefill the
 * bare input when the route's `example` omits the param, so the user gets a
 * working feed on first load instead of an empty box.
 */
export function parseDefaultFromDescription(description: string): string {
  if (!description) return '';
  const m = description.match(/默认[为是]?\s*`([^`]+)`/);
  if (!m) return '';
  const value = m[1].trim();
  // Reject obviously-not-a-value captures (empty, pure punctuation, or a bare
  // sentence fragment that slipped into backticks).
  if (!value || /^[\s.,;:，。；：]+$/.test(value)) return '';
  return value;
}

/**
 * Whether a route needs user-supplied credentials (cookie/token) beyond path
 * params — i.e. it will 503 until the RSSHub instance is configured. Currently
 * relies on RSSHub's `features.requireConfig` flag; returns a short reason for
 * the badge tooltip when truthy.
 */
export function requiresAuth(route: {
  route_path: string;
  features?: { requireConfig?: unknown } | null;
}): string | null {
  if (route.features?.requireConfig) return '需配置 Cookie/Token';
  return null;
}

/**
 * Routes verified broken against the self-hosted instance (2026-07-11, after
 * installing patchright): they return 503/404 regardless of params/cookies
 * because the upstream site changed its API, blocks RSSHub (403/HTML), or the
 * route's parsing code is broken. None is fixable by the user, so the explore
 * list hides them. Routes that merely need a cookie (see requiresAuth) or are
 * slow (browser-rendered, intermittent timeout) are NOT listed here — only
 * persistently broken ones. Re-verify before pruning: upstreams do recover.
 *
 * Note on xueqiu: user_stock/column stay hidden — user_stock hits
 * stock.xueqiu.com which IP-blacklists this server (403 even with a valid
 * xq_a_token); column is broken by Xuequi's anti-crawl (missing SNOWMAN_TARGET).
 * xueqiu/timeline is NOT here — it works once XUEQIU_COOKIES (incl. the HttpOnly
 * xq_a_token) is configured on the instance.
 *
 * Note on xueqiu/stock_info (2026-07-12): hidden — its handler calls
 * xueqiu.com/statuses/stock_timeline.json (or /search.json for type=all), both
 * of which sit behind Aliyun WAF. Verified: even with the instance's configured
 * XUEQIU_COOKIES (incl. xq_a_token) + a real browser UA + Referer, the JSON
 * endpoints return the WAF challenge page (an HTML `<textarea id="renderData">`
 * blob), so RSSHub's `res2.data.list` is undefined → `TypeError ... 'map'` → 503.
 * The route's Playwright `parseToken` (now that chromium 1223 is installed) does
 * launch, but the token it obtains still doesn't satisfy the WAF on the API
 * endpoint. No user config revives it; same anti-crawl wall as the other hidden
 * xueqiu token routes.
 *
 * Note on finology: the entire namespace is hidden (2026-07-12) — every route
 * returns 503 because upstream insider.finology.in 403-blocks RSSHub outright,
 * so no amount of config/cookies revives them.
 *
 * Note on spglobal: the entire namespace is hidden (2026-07-12) — its single
 * /spglobal/ratings/:language? route proxies spglobal.com's crownpeak search
 * API, which 403-blocks this server outright (verified: even a bare GET to
 * https://www.spglobal.com/ returns 403). RSSHub therefore always 503s
 * regardless of the language param or any cookie; nothing the user can do.
 *
 * Note on seekingalpha: hidden (2026-07-12) — the route's handler fetches
 * seekingalpha.com/api/v3/symbols/:symbol/:category after grabbing a machine
 * cookie, but the API 403-blocks this server outright regardless of the
 * symbol/category/cookie (verified: both /seekingalpha/TSM/news and the bare
 * /seekingalpha/TSM return 503 with a 403 FetchError). RSSHub flags it
 * antiCrawler; no user config revives it.
 *
 * Note on eastmoney 个人中心 / ttjj 用户动态 (2026-07-12): the five
 * user-personal-center routes are hidden together — none is usable from the
 * explore page because their sole param is a private user UID that a stock
 * user cannot discover, and the upstream behavior for an arbitrary UID is
 * broken either way: cfh/guba/ttjj/user return a persistent 503 (RSSHub
 * "Error Message"), while trpl/gather return HTTP 200 but with garbage items
 * — every entry is the upstream error page text "很抱歉，您访问的帖子不存在"
 * rather than that UID's real activity (verified with UIDs 12345 and
 * 1234567890). So a user filling any UID gets either an error or misleading
 * junk; not fixable by params/cookies.
 */
const KNOWN_BROKEN_ROUTES = new Set<string>([
  '/bse/:category?/:keyword?',
  '/stream-capital/search',
  '/jin10/topic/:id',
  '/21caijing/channel/:name{.+}?',
  '/barronschina/:id?',
  '/caijing/roll',
  '/dtcj/datahero/:category?',
  '/dtcj/datainsight/:id?',
  '/nbd/:id?',
  '/taoguba/blog/:id',
  '/xueqiu/snb/:id',
  '/xueqiu/stock_comments/:id',
  '/xueqiu/stock_info/:id/:type?',
  '/xueqiu/today',
  '/xueqiu/favorite/:id',
  '/xueqiu/user/:id/:type?',
  '/xueqiu/user_stock/:id',
  '/xueqiu/column/:id',
  '/bloomberg/authors/:id/:slug/:source?',
  '/followin/tag/:tagId/:lang?',
  '/followin/topic/:topicId/:lang?',
  '/finology/bullets',
  '/finology/category/:category',
  '/finology/most-viewed',
  '/finology/tag/:topic',
  '/spglobal/ratings/:language?',
  '/seekingalpha/:symbol/:category?',
  '/eastmoney/gerenzhongxin/trpl/:uid',
  '/eastmoney/gerenzhongxin/cfh/:uid',
  '/eastmoney/gerenzhongxin/guba/:uid',
  '/eastmoney/ttjj/user/:uid',
  '/eastmoney/gerenzhongxin/gather/:uid',
  // cs/video 取数正常，但 item 正文是内嵌 <video src="http://...">：fulltext
  // 重抓返回的 video 丢 controls/poster，详情抽屉里渲染成空白矩形看不到视频，
  // 且 src 是 http 在 https 页面被混合内容拦截。上游 video.cs.com.cn 视频流
  // 无法在页面内稳定播放，对用户等同不可用，故从探索页隐藏（2026-07-12）。
  '/cs/video/:category?',
]);

/** Whether a route is persistently broken and should be hidden from explore. */
export function isKnownBroken(route: { route_path: string }): boolean {
  return KNOWN_BROKEN_ROUTES.has(route.route_path);
}

/**
 * Routes whose feed content is English (verified 2026-07-12 by fetching each
 * route's example feed and scoring CJK vs ASCII-letter ratio of the item
 * titles+summaries; all listed routes return <15% CJK). Hidden from the
 * explore list because the app is a Chinese stock-analysis reader — English
 * feeds are out of scope. NOT broken: they return real items, just in the
 * wrong language for this audience, so they live here rather than in
 * KNOWN_BROKEN_ROUTES. Re-verify before pruning.
 *
 * Same-namespace caveat: language is per-route, not per-namespace.
 * fastbull/news is Chinese (kept); only fastbull/express-news is English.
 * followin is a bilingual aggregator — followin/news is Chinese (kept) and
 * survives a lang=zh test, but the /followin/ home feed and /followin/kol
 * default to English (lang=zh does not recover Chinese: home stays English,
 * kol returns 0 items), so both are hidden.
 */
const ENGLISH_ONLY_ROUTES = new Set<string>([
  '/ainvest/article',
  '/ainvest/news',
  '/blockworks/',
  '/bloomberg/:site?',
  '/bullionvault/gold-news/:category?',
  '/fastbull/express-news',
  '/finviz/:category?',
  '/finviz/news/:ticker',
  '/followin/:categoryId?/:lang?',
  '/followin/kol/:kolId/:lang?',
  '/fx-markets/:channel',
  '/jpmorganchase/',
  '/stockedge/daily-updates/news',
  '/unusualwhales/news',
]);

/** Whether a route's feed is English-only and thus out of scope for explore. */
export function isEnglishOnly(route: { route_path: string }): boolean {
  return ENGLISH_ONLY_ROUTES.has(route.route_path);
}

/**
 * Routes whose feed works (returns real items, mostly Chinese) but whose
 * content is not useful on a stock-analysis explore page — e.g. off-topic
 * feeds (e-commerce apparel, lifestyle blogging), a single fund's NAV that
 * means nothing without a known fund code, a user-group timeline that needs a
 * private id, or a calendar feed whose shape duplicates the news routes.
 * Hidden from explore because none earns a slot, but NOT broken and NOT
 * English — kept separate so re-verification doesn't conflate the reasons.
 */
const UNUSEFUL_ROUTES = new Set<string>([
  '/zhizhuan100/analytic', // 淘宝服饰白皮书等电商分析，与股市无关
  '/zhitongcaijing/:id?/:category?', // 探索页默认参数抓不到内容
  '/youzhiyouxing/materials/:id?', // 生活/情感博客，非股市
  '/xueqiu/fund/:id', // 单只基金净值，需已知基金代码，探索页无意义
  '/xueqiu/timeline/:usergroup_id?', // 用户组时间线，依赖私有 usergroup_id
  '/wallstreetcn/calendar/:section?', // 财经日历，格式特殊且与快讯路由重复
  '/ulapia/research/latest', // 券商研报，与 ulapia/reports 重复且研报页已覆盖
  '/ulapia/reports/:category?', // 同上
  '/baidu/gushitong/index', // 股市通首页指数，与个股/盘面分析重复
  '/bigquant/collections', // 量化因子专题，非资讯流
  '/chinamoney/:channelId?', // 外汇交易中心公告，与股市关联弱
  '/chinaratings/CreditResearch/:category{.+}?', // 中债城投研究，非个股资讯
  '/fastbull/news', // 中文财经新闻，与其他快讯路由重复
  '/futunn/video', // 视频流，探索页无法有效预览
  '/gelonghui/user/:id', // 用户文章，依赖私有用户 id，探索页抓不到
  '/gov/pbc/goutongjiaoliu', // 央行沟通交流，与股市关联弱
  '/gov/pbc/gzlw', // 央行工作论文，与股市关联弱
  '/huijin-inv/news', // 汇金公司公告，非股市资讯
  '/jisilu/category/:id', // 集思录分类讨论，社区内容
  '/jisilu/explore/:filter?', // 集思录广场，社区内容
  '/jisilu/people/:id/:type?', // 集思录用户动态，依赖私有 id
  '/jisilu/topic/:id', // 集思录话题，社区内容
  '/jiuyangongshe/community', // 九言公社社群，社区内容
  '/laohu8/personal/:id', // 老虎社区个人主页，依赖私有 id
  '/lhratings/research/:type?', // 联合评级研报，探索页抓不到
  '/mrm/:category?', // 储备冻肉轮换等商务公告，非股市
  '/nbd/daily', // 每日经济新闻重磅，探索页抓不到
  '/sse/convert/:query?', // 上交所可转债公告，与披露路由重复
  '/sselawsrules/:category{.+}?', // 上交所业务规则，非资讯流
  '/taoguba/:category?', // 淘股吧论坛，探索页抓不到
  '/szse/rule/:channel{.+}?', // 深交所业务规则，非资讯流
]);

/** Whether a route's feed is not useful for the stock explore page. */
export function isUnuseful(route: { route_path: string }): boolean {
  return UNUSEFUL_ROUTES.has(route.route_path);
}

/**
 * Whether a route should be hidden from the explore list — persistently
 * broken, English-only, or not useful for a stock reader. useRssNamespaces
 * filters on this so the list only shows Chinese feeds that work and matter.
 */
export function isHiddenFromExplore(route: { route_path: string }): boolean {
  return isKnownBroken(route) || isEnglishOnly(route) || isUnuseful(route);
}

/**
 * Routes whose path params lack a static option list in RSSHub metadata but
 * have a remote enumerable source we proxy. For these, the param form renders a
 * searchable picker (fetching the option list from our backend) instead of a
 * bare text input. Add a route here when a remote list endpoint is wired up.
 */
const REMOTE_OPTIONS_ROUTES = new Set<string>([
  '/gelonghui/subject/:id', // 主题列表来自格隆汇 /api/subjects（后端代理 + 6h 缓存）
  '/cih-index/report/list/:report?', // 一级分类来自中指指数报告页 indNavLists（后端代理 + 6h 缓存）；report 是复合路径段，picker 负责拼 f<id>-p1-oaddtime-ddesc
  '/cls/subject/:id?', // 话题列表由默认话题文章收割自财联社文章 API（后端代理 + 6h 缓存）；id 可选，留空走默认盘面直播
  '/futunn/topic/:id', // 话题列表来自富途 news-site-api/main/get-topics-list（后端代理 + 6h 缓存，分页累积）；id 必填
]);

/** Whether a route has a remote option list to power a searchable param picker. */
export function routeNeedsRemoteOptions(routePath: string): boolean {
  return REMOTE_OPTIONS_ROUTES.has(routePath);
}

/**
 * Routes whose params form a dependent cascade (type2 depends on type1) and have
 * a remote tree endpoint we proxy. For these, the param form renders a single
 * cascade picker (one component for all params) instead of per-param inputs — a
 * bare per-param input would let the user pick an illegal combo (e.g.
 * `HOT`/`WEEK_black`) that makes upstream return empty (RSSHub 503).
 */
const CASCADE_PICKER_ROUTES = new Set<string>([
  '/nanhua/report/:type1/:type2', // 分类树来自南华 getTreeList（后端代理 + 6h 缓存）
]);

/** Whether a route needs a single cascade picker covering all its params. */
export function routeCascadePicker(routePath: string): boolean {
  return CASCADE_PICKER_ROUTES.has(routePath);
}

/**
 * Reduce a route's Markdown `description` to a single prose line for display.
 * Drops RSSHub `:::` containers (tip/warning/info — their content is either
 * usage examples redundant with the param form, or auth notes already covered
 * by the requiresAuth badge) and Markdown table rows (rendered as Select
 * options via parseMarkdownParamOptions, not as text).
 */
export function routeDescriptionProse(markdown: string): string {
  if (!markdown) return '';
  const kept: string[] = [];
  let inContainer = false;
  for (const rawLine of markdown.split(/\r?\n/)) {
    const trimmed = rawLine.trim();
    // ::: fence — toggles a container block. Skip the fence line itself.
    if (trimmed.startsWith(':::')) {
      inContainer = !inContainer;
      continue;
    }
    if (inContainer) continue; // inside a container — drop its body
    if (trimmed.startsWith('|')) continue; // table row → Select options
    kept.push(rawLine);
  }
  return kept.join(' ').replace(/\s+/g, ' ').trim();
}
