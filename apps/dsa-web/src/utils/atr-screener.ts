/**
 * ATR 相对波动率选股指标 — 通达信公式前端实现
 *
 * 公式参数:
 *   N = 14     (ATR 周期)
 *   D = 250    (回看交易日天数)
 *   R = 60     (达标比例 %)
 *   阈值 = 2.8 (高波动预警线, ATR%)
 *
 * 筛选条件:
 *   最近 D 日中, ATR相对波动率 > 2.8 的天数占比 >= R%
 *   且上市满 D 个交易日
 */

// ─── 参数常量 ───

export const ATR_PERIOD = 14;
export const LOOKBACK_DAYS = 250;
export const REQUIRED_RATIO = 60; // percent
export const VOLATILITY_THRESHOLD = 2.8; // ATR%
export const MIN_BARS = LOOKBACK_DAYS + 1; // 251, need REF(C,1)
export const SCREEN_GROUP_NAME = '高波动股';

// ─── 数据类型 ───

/** K-line bar: [date_str, open, high, low, close] */
export type KlineCompact = [string, number, number, number, number];

export interface AtrScreenResult {
  /** 符合条件的股票代码列表 */
  matchedCodes: string[];
  /** 所有参与计算的股票详情 */
  stockDetails: AtrStockDetail[];
  /** 分析的总股票数 */
  totalAnalyzed: number;
}

export interface AtrStockDetail {
  code: string;
  /** 当前 ATR% */
  currentAtrPct: number;
  /** 最近 D 日中达标天数 */
  qualifiedDays: number;
  /** 达标比例 */
  qualifiedRatio: number;
  /** 是否满足筛选条件 */
  matched: boolean;
}

// ─── 核心计算 ───

/**
 * 计算单只股票的 ATR 相对波动率选股结果
 *
 * @param klines 按时间升序排列的 K 线数据 [date, open, high, low, close]
 * @returns 该股票的筛选详情, 若不满足数据量要求则返回 null
 */
export function calculateAtrForStock(
  klines: KlineCompact[],
  atrPeriod = ATR_PERIOD,
  lookbackDays = LOOKBACK_DAYS,
  threshold = VOLATILITY_THRESHOLD,
): AtrStockDetail | null {
  if (klines.length < lookbackDays + 1) {
    return null;
  }

  const n = klines.length;

  // Step 1: Calculate True Range for each day (from index 1 onwards, needs REF(C,1))
  const tr: number[] = new Array(n);
  tr[0] = klines[0][3] - klines[0][2]; // high - low for first day (no prev close)
  for (let i = 1; i < n; i++) {
    const high = klines[i][2];
    const low = klines[i][3];
    const prevClose = klines[i - 1][4];
    const hl = high - low;
    const hpc = Math.abs(high - prevClose);
    const lpc = Math.abs(low - prevClose);
    tr[i] = Math.max(hl, hpc, lpc);
  }

  // Step 2: Calculate ATR (Simple Moving Average of TR, like 通达信 MA)
  const atr: number[] = new Array(n);
  // First ATR = average of first `atrPeriod` TR values
  let sumTR = 0;
  for (let i = 0; i < atrPeriod; i++) {
    sumTR += tr[i];
  }
  atr[atrPeriod - 1] = sumTR / atrPeriod;

  for (let i = atrPeriod; i < n; i++) {
    atr[i] = (atr[i - 1] * (atrPeriod - 1) + tr[i]) / atrPeriod;
  }

  // Step 3: Calculate ATR relative volatility (%)
  const atrPct: number[] = new Array(n);
  for (let i = atrPeriod - 1; i < n; i++) {
    const close = klines[i][4];
    atrPct[i] = close > 0 ? (atr[i] / close) * 100 : 0;
  }

  // Step 4: Check condition over the last `lookbackDays`
  const startIdx = n - lookbackDays;
  let qualifiedDays = 0;
  for (let i = startIdx; i < n; i++) {
    if (atrPct[i] > threshold) {
      qualifiedDays++;
    }
  }

  const qualifiedRatio = (qualifiedDays / lookbackDays) * 100;
  const currentAtrPct = atrPct[n - 1];
  const matched = qualifiedRatio >= REQUIRED_RATIO;

  return {
    code: klines.length > 0 ? '' : '', // placeholder, set by caller
    currentAtrPct: Math.round(currentAtrPct * 100) / 100,
    qualifiedDays,
    qualifiedRatio: Math.round(qualifiedRatio * 100) / 100,
    matched,
  };
}

/**
 * Run ATR screener over all stocks' kline data
 *
 * @param klineMap Map of stock code -> KlineCompact[]
 * @returns Screen result with matched codes and details
 */
export function runAtrScreener(
  klineMap: Map<string, KlineCompact[]> | Record<string, KlineCompact[]>,
): AtrScreenResult {
  const entries = klineMap instanceof Map
    ? Array.from(klineMap.entries())
    : Object.entries(klineMap);

  const stockDetails: AtrStockDetail[] = [];
  const matchedCodes: string[] = [];

  for (const [code, klines] of entries) {
    const detail = calculateAtrForStock(klines);
    if (!detail) continue;

    detail.code = code;
    stockDetails.push(detail);

    if (detail.matched) {
      matchedCodes.push(code);
    }
  }

  return {
    matchedCodes,
    stockDetails,
    totalAnalyzed: stockDetails.length,
  };
}
