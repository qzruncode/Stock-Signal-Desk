// ---------------------------------------------------------------------------
// Index data
// ---------------------------------------------------------------------------

export interface IndexPoint {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  pct_chg: number | null;
  volume: number | null;
  amount?: number | null;
  change_amount?: number | null;
}

export interface IndexDataResponse {
  index_code: string;
  index_name: string;
  latest: IndexPoint;
  history: IndexPoint[];
  pe_ttm: number | null;
  pb: number | null;
  _fetched_at: string;
  _cached: boolean;
  source: string;
  errors: string[];
}

export type IndexCode = "000001" | "399001" | "399006" | "000688";

export const INDEX_OPTIONS: Record<IndexCode, string> = {
  "000001": "上证指数",
  "399001": "深证成指",
  "399006": "创业板指",
  "000688": "科创50",
};

// ---------------------------------------------------------------------------
// Bond yield
// ---------------------------------------------------------------------------

export interface BondYieldPoint {
  date: string;
  value: number;
}

export interface BondYieldResponse {
  country: string;
  term: string;
  latest_yield: number | null;
  history: BondYieldPoint[];
  spread: number | null; // 10y - 2y
  _fetched_at: string;
  _cached: boolean;
  source: string;
  errors: string[];
}

export const BOND_COUNTRY_OPTIONS: Record<string, string> = {
  cn: "中国",
  us: "美国",
};

export const BOND_TERM_OPTIONS: Record<string, string> = {
  "2y": "2年期",
  "5y": "5年期",
  "10y": "10年期",
  "30y": "30年期",
};

// ---------------------------------------------------------------------------
// Macro indicator
// ---------------------------------------------------------------------------

export interface IndicatorPoint {
  period: string;
  value: number | null;
  yoy?: number | null;
  mom?: number | null;
  extra?: Record<string, number | null>;
}

export interface IndicatorResponse {
  indicator: string;
  indicator_name: string;
  latest: IndicatorPoint;
  history: IndicatorPoint[];
  trend: string;
  _fetched_at: string;
  _cached: boolean;
  source: string;
  errors: string[];
}

export const INDICATOR_OPTIONS: Record<string, string> = {
  PMI: "制造业PMI",
  CPI: "居民消费价格指数",
  PPI: "工业生产者出厂价格",
  GDP: "国内生产总值",
  M2: "货币供应量(M2)",
  "社融": "社会融资规模",
  LPR: "贷款市场报价利率",
};

// ---------------------------------------------------------------------------
// Sector fund flow
// ---------------------------------------------------------------------------

export interface SectorFlowRecord {
  name: string;
  pct_chg: number | null;
  main_net_inflow: number | null;
  super_large_net_inflow: number | null;
  large_net_inflow: number | null;
  total_amount?: number | null;
  up_count?: number | null;
  down_count?: number | null;
  leading_stock?: string | null;
}

export interface SectorFlowResponse {
  type: string;
  top_n: number;
  inflow_top: SectorFlowRecord[];
  outflow_top: SectorFlowRecord[];
  records: SectorFlowRecord[]; // compat
  _fetched_at: string;
  _cached: boolean;
  source: string;
  errors: string[];
}

// ---------------------------------------------------------------------------
// Market breadth
// ---------------------------------------------------------------------------

export interface MarketBreadthResponse {
  up_count: number | null;
  down_count: number | null;
  flat_count?: number | null;
  advance_decline_ratio: number | null;
  new_high_60d?: number | null;
  new_low_60d?: number | null;
  consecutive_up_days?: number | null;
  consecutive_down_days?: number | null;
  limit_up_count: number | null;
  limit_down_count: number | null;
  broken_board_rate: number | null;
  volume: number | null;
  _fetched_at: string;
  _cached: boolean;
  source: string;
  errors: string[];
}
