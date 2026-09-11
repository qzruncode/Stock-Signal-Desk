/**
 * Analysis-related type definitions.
 * Aligned with the API schema.
 */

// ============ Report Types ============

export type ReportType = 'simple' | 'detailed' | 'full' | 'brief' | 'market_review' | 'conversation';

export type ReportLanguage = 'zh' | 'en';

/** Report metadata */
export interface ReportMeta {
  id?: number;  // Analysis history record ID, present for persisted reports
  queryId: string;
  stockCode: string;
  stockName: string;
  reportType: ReportType;
  reportLanguage?: ReportLanguage;
  createdAt: string;
  currentPrice?: number;
  changePct?: number;
  modelUsed?: string;  // LLM model used for analysis
}

/** Sentiment label */
export type SentimentLabel =
  | '极度悲观'
  | '悲观'
  | '中性'
  | '乐观'
  | '极度乐观'
  | 'Very Bearish'
  | 'Bearish'
  | 'Neutral'
  | 'Bullish'
  | 'Very Bullish';

/** Report summary section */
export interface ReportSummary {
  analysisSummary: string;
  operationAdvice: string;
  trendPrediction: string;
  sentimentScore: number;
  sentimentLabel?: SentimentLabel;
}

/** Strategy section */
export interface ReportStrategy {
  idealBuy?: string;
  secondaryBuy?: string;
  stopLoss?: string;
  takeProfit?: string;
}

export interface RelatedBoard {
  name: string;
  code?: string;
  type?: string;
}

export interface SectorRankingItem {
  name: string;
  changePct?: number;
}

export interface SectorRankings {
  top?: SectorRankingItem[];
  bottom?: SectorRankingItem[];
}

/** Details section */
export interface ReportDetails {
  newsContent?: string;
  rawResult?: Record<string, unknown>;
  contextSnapshot?: Record<string, unknown>;
  financialReport?: Record<string, unknown>;
  dividendMetrics?: Record<string, unknown>;
  belongBoards?: RelatedBoard[];
  sectorRankings?: SectorRankings;
}

/** Conversation record (prompt + response) */
export interface ConversationRecord {
  prompt: string;
  response: string;
  modelUsed?: string;
  templateName?: string;
  templateId?: string;
}

/** Full analysis report */
export interface AnalysisReport {
  meta: ReportMeta;
  summary: ReportSummary;
  strategy?: ReportStrategy;
  details?: ReportDetails;
  conversation?: ConversationRecord;
}

// ============ Error Types ============

export interface ApiError {
  error: string;
  message: string;
  detail?: Record<string, unknown>;
}

// ============ Helper Functions ============

/** Get sentiment label by score */
export const getSentimentLabel = (score: number, language: ReportLanguage = 'zh'): SentimentLabel => {
  if (language === 'en') {
    if (score <= 20) return 'Very Bearish';
    if (score <= 40) return 'Bearish';
    if (score <= 60) return 'Neutral';
    if (score <= 80) return 'Bullish';
    return 'Very Bullish';
  }
  if (score <= 20) return '极度悲观';
  if (score <= 40) return '悲观';
  if (score <= 60) return '中性';
  if (score <= 80) return '乐观';
  return '极度乐观';
};

/** Get sentiment color by score */
export const getSentimentColor = (score: number): string => {
  if (score <= 20) return '#ef4444'; // red-500
  if (score <= 40) return '#f97316'; // orange-500
  if (score <= 60) return '#eab308'; // yellow-500
  if (score <= 80) return '#22c55e'; // green-500
  return '#10b981'; // emerald-500
};
