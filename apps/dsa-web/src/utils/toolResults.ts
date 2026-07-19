/**
 * 工具结果类型(按后端 _compact_tool_result 实际输出 dict 定义)
 * 与格式化工具。纯数据/函数,不含组件,供 tool-ui 组件复用。
 */

/** 后端 get_kline compact 后的 result dict */
export interface KlineToolResult {
  symbol: string;
  count: number;
  latest?: { date: string; open: number; close: number; high: number; low: number; volume: number | null; pct_chg?: number | null } & Record<string, unknown>;
  recent?: Array<{ date: string; open: number; close: number; high: number; low: number; volume: number | null; pct_chg?: number | null; turnover_rate?: number | null; ma5?: number | null; ma10?: number | null; ma20?: number | null }>;
  range?: { start: string; end: string };
  is_stale?: boolean;
  _cached?: boolean;
}

/** 后端 get_realtime_quotes compact 后单个 item */
export interface RealtimeQuoteItem {
  symbol: string;
  name: string;
  price: number | null;
  change: number | null;
  pct_chg: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  volume: number | null;
  amount: number | null;
  turnover_rate?: number | null;
  pe_dynamic?: number | null;
  pb?: number | null;
  total_mv?: number | null;
  circ_mv?: number | null;
}
export interface RealtimeQuotesToolResult {
  total: number;
  items: RealtimeQuoteItem[];
  data_time?: string;
  is_stale?: boolean;
  _cached?: boolean;
}

/** 后端 get_financials compact 后的 result dict */
export interface FinancialPeriod {
  report_date?: string | null;
  net_profit?: number | null;
  net_profit_yoy?: number | null;
  revenue?: number | null;
  revenue_yoy?: number | null;
  eps?: number | null;
  roe?: number | null;
  gross_margin?: number | null;
  net_margin?: number | null;
  debt_ratio?: number | null;
  [k: string]: unknown;
}
export interface FinancialsToolResult {
  symbol: string;
  periods: number;
  latest?: FinancialPeriod;
  recent_periods?: FinancialPeriod[];
  _cached?: boolean;
}

/** 后端 search_news compact 后的单条新闻 */
export interface NewsToolItem {
  title: string;
  publish_time?: string | null;
  source?: string;
  category?: string;
  event_type?: string;
  polarity?: string;
  importance?: string;
  summary?: string;
}
export interface NewsToolResult {
  symbol: string;
  days?: number;
  sentiment_score?: number | null;
  overall_score?: number | null;
  positive_count?: number;
  negative_count?: number;
  neutral_count?: number;
  items?: NewsToolItem[];
  item_count?: number;
  _cached?: boolean;
}

export function formatNum(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '-';
  return value.toFixed(digits);
}

export function formatAmount(value: number | null | undefined): string {
  if (value == null) return '-';
  const abs = Math.abs(value);
  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${(value / 1e4).toFixed(2)}万`;
  return value.toFixed(0);
}

export function formatPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '-';
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

/** 后端 search_financial_news 的单条聚合资讯。 */
export interface RssFeedItem {
  id?: string;
  title: string;
  summary?: string;
  content_text?: string;
  link?: string;
  published?: string | null;
  source?: string;
  image?: string;
  author?: string;
  tags?: string[];
  attachments?: Array<{ url: string; mime_type: string; title?: string }>;
  rss_route?: string;
  rss_params?: Record<string, unknown>;
  source_type?: 'rss' | 'websearch';
  content_fallback?: boolean;
}
export interface RssFeedSourceCoverage {
  item_count?: number;
  recent_item_count?: number;
  relevant_item_count?: number;
  success?: boolean;
}
export interface RssFeedToolResult {
  query?: string;
  topic?: string;
  days?: number;
  item_count?: number;
  items?: RssFeedItem[];
  errors?: string[];
  warnings?: string[];
  success?: boolean;
  fallback_attempted?: boolean;
  fallback_used?: boolean;
  rss_routes?: RssFeedSourceCoverage[];
  source_coverage?: RssFeedSourceCoverage[];
}

/** Explain a valid empty search without conflating it with a source failure. */
export function describeRssEmptyResult(result: RssFeedToolResult, isResearchLibrary: boolean): string {
  const coverage = result.rss_routes ?? result.source_coverage ?? [];
  const rawCount = coverage.reduce((sum, row) => sum + (row.item_count ?? 0), 0);
  const successfulSources = coverage.filter((row) => row.success).length;
  const rowsWithRecentCount = coverage.filter((row) => row.recent_item_count != null);
  const rowsWithRelevantCount = coverage.filter((row) => row.relevant_item_count != null);
  const recentCount = rowsWithRecentCount.reduce((sum, row) => sum + (row.recent_item_count ?? 0), 0);
  const relevantCount = rowsWithRelevantCount.reduce((sum, row) => sum + (row.relevant_item_count ?? 0), 0);

  let reason: string;
  if (rawCount > 0 && rowsWithRecentCount.length > 0 && recentCount === 0) {
    reason = `数据源返回 ${rawCount} 条，但均不在最近 ${result.days ?? '-'} 天内`;
  } else if (rawCount > 0 && rowsWithRelevantCount.length > 0 && relevantCount === 0) {
    reason = `数据源返回 ${rawCount} 条，但时间窗内没有主题匹配`;
  } else if (rawCount > 0) {
    reason = `数据源返回 ${rawCount} 条，但没有匹配当前查询`;
  } else if (successfulSources > 0 || result.success) {
    reason = '数据源连接正常，本次查询没有匹配记录';
  } else {
    reason = isResearchLibrary ? '研究资料检索失败' : '财经资讯获取失败';
  }

  if (result.fallback_attempted && !result.fallback_used) {
    return `${reason}；联网兜底也未找到结果`;
  }
  return reason;
}
