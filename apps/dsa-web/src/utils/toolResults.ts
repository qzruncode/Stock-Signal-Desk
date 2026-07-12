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
  pe?: number | null;
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

/** 后端 get_buy_criteria_analysis 的单维度结果 */
export interface BuyCriteriaItem {
  criterion_id: string;
  criterion_name: string;
  index: number;
  passed: boolean;
  verdict: string;
  evidence: { data_summary: string };
  analyzed_at: string;
}
export interface BuyCriteriaToolResult {
  symbol: string;
  stock_name?: string | null;
  trade_date: string;
  cached?: boolean;
  final_decision: '可买入' | '不可买入';
  passed_count: number;
  failed_count: number;
  not_evaluated_count: number;
  stopped_at: string | null;
  summary: string;
  criteria: BuyCriteriaItem[];
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

/** 后端 read_rss_feed 的单条 item（_compact_tool_result 截断后的字段）。 */
export interface RssFeedItem {
  title: string;
  summary?: string;
  link?: string;
  published?: string | null;
  source?: string;
  image?: string;
}
export interface RssFeedToolResult {
  feed_title?: string;
  feed_link?: string;
  item_count?: number;
  items?: RssFeedItem[];
  errors?: string[];
  _cached?: boolean;
}

/** 后端 read_rss_item 的全文结果。 */
export interface RssItemToolResult {
  title: string;
  content_text?: string;
  link?: string;
  published?: string | null;
  source?: string;
  _fallback?: boolean;
  _truncated?: boolean;
  _not_found?: boolean;
  errors?: string[];
}
