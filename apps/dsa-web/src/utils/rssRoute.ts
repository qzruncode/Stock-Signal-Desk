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
