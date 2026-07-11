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

/**
 * Routes that depend on a logged-in account cookie/token even when RSSHub does
 * not flag them with `features.requireConfig` — i.e. they would work if the user
 * configured the corresponding env var, and are NOT already hidden as broken.
 * Empirically maintained: only add routes confirmed to (a) need credentials and
 * (b) actually return content once configured. (xueqiu routes are NOT here —
 * verified that XUEQIU_COOKIES does not revive them due to IP blacklist + missing
 * HttpOnly xq_a_token; they live in KNOWN_BROKEN_ROUTES instead.)
 */
const AUTH_REQUIRED_ROUTES = new Set<string>([]);

/**
 * Whether a route needs user-supplied credentials (cookie/token) beyond path
 * params — i.e. it will 503 until the RSSHub instance is configured. Covers both
 * RSSHub's `features.requireConfig` flag and the auth-dependent routes RSSHub
 * fails to flag. Returns a short reason for the badge tooltip when truthy.
 */
export function requiresAuth(route: {
  route_path: string;
  features?: { requireConfig?: unknown } | null;
}): string | null {
  if (route.features?.requireConfig) return '需配置 Cookie/Token';
  // Match on the namespace + first literal segment so optional params
  // (e.g. /xueqiu/column/:id) still hit /xueqiu/column.
  const head = route.route_path.split('/:')[0].replace(/\/+$/, '');
  if (AUTH_REQUIRED_ROUTES.has(head)) return '需登录 Cookie';
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
  '/xueqiu/today',
  '/xueqiu/favorite/:id',
  '/xueqiu/user/:id/:type?',
  '/xueqiu/user_stock/:id',
  '/xueqiu/column/:id',
  '/bloomberg/authors/:id/:slug/:source?',
  '/followin/tag/:tagId/:lang?',
  '/followin/topic/:topicId/:lang?',
  '/finology/category/:category',
  '/finology/most-viewed',
  '/finology/tag/:topic',
]);

/** Whether a route is persistently broken and should be hidden from explore. */
export function isKnownBroken(route: { route_path: string }): boolean {
  return KNOWN_BROKEN_ROUTES.has(route.route_path);
}

/** Remove Markdown tables before displaying a route's prose description. */
export function routeDescriptionProse(markdown: string): string {
  if (!markdown) return '';
  return markdown
    .split(/\r?\n/)
    .filter((line) => !line.trim().startsWith('|'))
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim();
}
