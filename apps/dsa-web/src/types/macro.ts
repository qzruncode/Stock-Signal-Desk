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
  spread: number | null; // 10y - 1y
  _fetched_at: string;
  _cached: boolean;
  source: string;
  errors: string[];
}

export const BOND_COUNTRY_OPTIONS: Record<string, string> = {
  cn: "中国",
  // us: "美国", // TODO: FRED API
};

export const BOND_TERM_OPTIONS: Record<string, string> = {
  "1y": "1年期",
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
