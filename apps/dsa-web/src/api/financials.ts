import apiClient from './index';

export interface FinancialItem {
  report_date: string | null;
  net_profit: number | null;
  net_profit_yoy: number | null;
  parent_net_profit: number | null;
  parent_net_profit_yoy: number | null;
  deducted_profit: number | null;
  deducted_profit_yoy: number | null;
  deducted_net_profit: number | null;
  deducted_net_profit_yoy: number | null;
  revenue: number | null;
  revenue_yoy: number | null;
  revenue_qoq: number | null;
  eps: number | null;
  bps: number | null;
  capital_reserve_per_share: number | null;
  undistributed_profit_per_share: number | null;
  operating_cf_per_share: number | null;
  net_margin: number | null;
  gross_margin: number | null;
  roe: number | null;
  roe_diluted: number | null;
  operating_cycle: number | null;
  inventory_turnover: number | null;
  inventory_turnover_days: number | null;
  receivables_turnover_days: number | null;
  current_ratio: number | null;
  quick_ratio: number | null;
  conservative_quick_ratio: number | null;
  equity_ratio: number | null;
  debt_ratio: number | null;
  operating_cash_flow: number | null;
  accounts_receivable: number | null;
  inventory: number | null;
  contract_liabilities: number | null;
  asset_impairment_loss: number | null;
}

export interface FinancialsResponse {
  symbol: string;
  periods: number;
  items: FinancialItem[];
  _fetched_at?: string;
  _cached?: boolean;
}

export const financialsApi = {
  async getFinancials(
    symbol: string,
    periods: number = 12,
    force: boolean = false,
  ): Promise<FinancialsResponse> {
    const response = await apiClient.get<FinancialsResponse>(
      '/api/v1/stocks/financials',
      { params: { symbol, periods, force }, timeout: 30000 },
    );
    return response.data;
  },
};

// ---------------------------------------------------------------------------
// Valuation ratios + shareholder structure
// ---------------------------------------------------------------------------

export interface ValuationRatiosResponse {
  symbol: string;
  trade_date: string | null;
  pe_static: number | null;
  pe_dynamic: number | null;
  pe_ttm: number | null;
  pb: number | null;
  ps: number | null;
  pcf: number | null;
  peg: number | null;
  dividend_yield: number | null;
  dividend_date?: string | null;
  pe_percentiles: Record<string, number>;
  industry?: string | null;
  industry_average: {
    industry: string | null;
    pe: number | null;
    pb: number | null;
    sample_size: number;
  };
  price_overdraft_signal?: {
    status: 'low' | 'watch' | 'medium' | 'high' | 'uncertain';
    score: number;
    confidence: number;
    valuation_expensive_score: number;
    expectation_support_score: number;
    signals: string[];
    metrics: {
      pe_ttm: number | null;
      pe_dynamic: number | null;
      pb: number | null;
      peg: number | null;
      dividend_yield: number | null;
      pe_percentile_1y: number | null;
      pe_percentile_3y: number | null;
      pe_percentile_5y: number | null;
      industry_pe: number | null;
      industry_pb: number | null;
      pe_premium_vs_industry: number | null;
      pb_premium_vs_industry: number | null;
      dynamic_pe_discount_vs_ttm: number | null;
    };
    reasoning: string[];
    limitations: string[];
  };
  source_chain?: string[];
  errors?: string[];
  _fetched_at?: string;
  _cached?: boolean;
}

export interface TopHolderItem {
  name: string;
  holding_pct: number | null;
  holding_amount: number | null;
  holder_type: string | null;
  change: string | null;
}

export interface MajorHolderChangeItem {
  date: string | null;
  holder: string;
  direction: string | null;
  shares: number | null;
  pct: number | null;
  price: number | null;
}

export interface ShareholderStructureResponse {
  symbol: string;
  holder_count: number | null;
  holder_count_previous: number | null;
  holder_count_change: number | null;
  holder_count_change_pct: number | null;
  holder_report_date: string | null;
  top10_holders: TopHolderItem[];
  institution_holding_pct: number | null;
  major_holder_changes: MajorHolderChangeItem[];
  actual_controller: string | null;
  source_chain?: string[];
  errors?: string[];
  _fetched_at?: string;
  _cached?: boolean;
}

export const valuationApi = {
  async getValuationRatios(
    symbol: string,
    withHistory: boolean = true,
    force: boolean = false,
  ): Promise<ValuationRatiosResponse> {
    const response = await apiClient.get<ValuationRatiosResponse>(
      '/api/v1/stocks/valuation-ratios',
      { params: { symbol, with_history: withHistory, force }, timeout: 45000 },
    );
    return response.data;
  },
};

export const shareholderApi = {
  async getShareholderStructure(
    symbol: string,
    force: boolean = false,
  ): Promise<ShareholderStructureResponse> {
    const response = await apiClient.get<ShareholderStructureResponse>(
      '/api/v1/stocks/shareholder-structure',
      { params: { symbol, force }, timeout: 45000 },
    );
    return response.data;
  },
};

// ---------------------------------------------------------------------------
// Financial Statements (三大财务报表)
// ---------------------------------------------------------------------------

export interface BalanceSheetItem {
  report_date: string | null;
  report_date_name: string | null;
  total_assets: number | null;
  total_liabilities: number | null;
  total_equity: number | null;
  parent_equity: number | null;
  monetary_funds: number | null;
  accounts_receivable: number | null;
  inventory: number | null;
  contract_liabilities: number | null;
  fixed_asset: number | null;
  short_loan: number | null;
  long_loan: number | null;
  accounts_payable: number | null;
  noncurrent_liab_1year: number | null;
  lease_liab: number | null;
  total_current_assets: number | null;
  total_current_liabilities: number | null;
  debt_ratio: number | null;
  equity_multiplier: number | null;
}

export interface IncomeStatementItem {
  report_date: string | null;
  report_date_name: string | null;
  revenue: number | null;
  revenue_yoy?: number | null;
  total_cost: number | null;
  operate_cost: number | null;
  operate_profit: number | null;
  total_profit: number | null;
  net_profit: number | null;
  net_profit_yoy?: number | null;
  parent_net_profit: number | null;
  parent_net_profit_yoy?: number | null;
  deducted_net_profit: number | null;
  deducted_net_profit_yoy?: number | null;
  basic_eps: number | null;
  diluted_eps: number | null;
  sale_expense: number | null;
  manage_expense: number | null;
  research_expense: number | null;
  finance_expense: number | null;
  invest_income: number | null;
  operate_tax_add: number | null;
  income_tax: number | null;
  asset_impairment_loss?: number | null;
  gross_profit: number | null;
  gross_margin: number | null;
  net_margin: number | null;
}

export interface CashflowItem {
  report_date: string | null;
  report_date_name: string | null;
  operating_cf: number | null;
  investing_cf: number | null;
  financing_cf: number | null;
  capex: number | null;
  net_profit: number | null;
  free_cashflow: number | null;
  cf_quality: number | null;
}

export interface FinancialStatementsResponse {
  symbol: string;
  periods: number;
  balance_sheet: BalanceSheetItem[];
  income_statement: IncomeStatementItem[];
  cashflow: CashflowItem[];
  source?: string;
  _fetched_at?: string;
  _cached?: boolean;
  _errors?: string[];
}

// ---------------------------------------------------------------------------
// Stock News
// ---------------------------------------------------------------------------

export interface NewsItem {
  title: string;
  summary: string;
  publish_time: string | null;
  source: string;
  url: string;
  category?: string;
}

export interface NewsResponse {
  symbol: string;
  days: number;
  source: string;
  items: NewsItem[];
  analysis?: Record<string, unknown>;
  source_chain?: string[];
  errors?: string[];
  _fetched_at?: string;
  _cached?: boolean;
}

export const newsApi = {
  async searchNews(
    symbol: string,
    days: number = 7,
    source: string = 'all',
    force: boolean = false,
  ): Promise<NewsResponse> {
    const response = await apiClient.get<NewsResponse>(
      '/api/v1/stocks/news',
      { params: { symbol, days, source, force }, timeout: 30000 },
    );
    return response.data;
  },
};

// ---------------------------------------------------------------------------
// Company Announcements
// ---------------------------------------------------------------------------

export interface AnnouncementItem {
  title: string;
  notice_type: string;
  publish_date: string | null;
  url: string;
  source?: string;
  event_type?: string;
  event_label?: string;
  polarity?: 'positive' | 'negative' | 'neutral';
  sentiment_score?: number;
  importance?: 'high' | 'medium' | 'low';
  tags?: string[];
}

export interface AnnouncementKeyEvent {
  title: string;
  date: string | null;
  source: string;
  event_type: string;
  polarity: 'positive' | 'negative' | 'neutral';
  importance: 'high' | 'medium' | 'low';
  tags: string[];
}

export interface AnnouncementsAnalysis {
  dimension: string;
  data_quality: {
    item_count: number;
    source_count: number;
    days: number;
    coverage_level: 'none' | 'thin' | 'fair' | 'good';
    proxy_item_count: number;
  };
  source_distribution: Record<string, number>;
  notice_type_distribution: Record<string, number>;
  event_distribution: Record<string, number>;
  polarity_distribution: Record<string, number>;
  importance_distribution: Record<string, number>;
  daily_distribution: Record<string, number>;
  key_events: AnnouncementKeyEvent[];
  ai_summary_hints: string[];
}

export interface AnnouncementsResponse {
  symbol: string;
  days: number;
  type: string;
  items: AnnouncementItem[];
  analysis?: AnnouncementsAnalysis;
  source_chain?: string[];
  errors?: string[];
  data_time?: string | null;
  is_stale?: boolean;
  fallback_used?: boolean;
  _fetched_at?: string;
  _cached?: boolean;
}

export const announcementsApi = {
  async getAnnouncements(
    symbol: string,
    days: number = 90,
    type: string = 'all',
    force: boolean = false,
  ): Promise<AnnouncementsResponse> {
    const response = await apiClient.get<AnnouncementsResponse>(
      '/api/v1/stocks/announcements',
      { params: { symbol, days, type, force }, timeout: 30000 },
    );
    return response.data;
  },
};

export interface RiskEventItem {
  title: string;
  summary: string;
  risk_summary: string;
  date: string | null;
  source: string;
  source_type: 'news' | 'announcement';
  url: string;
  severity: 'high' | 'medium' | 'low';
  risk_category: string;
  risk_label: string;
  event_type: string;
  tags: string[];
}

export interface RiskEventsResponse {
  symbol: string;
  days: number;
  items: RiskEventItem[];
  analysis: {
    total_events: number;
    severity_distribution: {
      high: number;
      medium: number;
      low: number;
    };
    source_distribution?: {
      news: number;
      announcement: number;
    };
    top_risk_labels: string[];
    high_severity_titles: string[];
    ai_summary_hints: string[];
  };
  source_chain?: string[];
  errors?: string[];
  _fetched_at?: string;
  _cached?: boolean;
}

export const riskEventsApi = {
  async getRiskEvents(
    symbol: string,
    days: number = 90,
    force: boolean = false,
  ): Promise<RiskEventsResponse> {
    const response = await apiClient.get<RiskEventsResponse>(
      '/api/v1/stocks/risk-events',
      { params: { symbol, days, force }, timeout: 45000 },
    );
    return response.data;
  },
};

// ---------------------------------------------------------------------------
// Sentiment Analysis
// ---------------------------------------------------------------------------

export interface SentimentItem {
  title: string;
  sentiment_score: number;
  label: 'positive' | 'negative' | 'neutral';
  source: string;
}

export interface DailyTrendItem {
  date: string;
  total: number;
  positive: number;
  negative: number;
  neutral: number;
}

export interface SentimentResponse {
  symbol: string;
  days: number;
  sentiment_score: number;
  positive_count: number;
  negative_count: number;
  neutral_count: number;
  daily_trend: DailyTrendItem[];
  top_keywords: string[];
  items: SentimentItem[];
  analysis?: Record<string, unknown>;
  errors?: string[];
  _fetched_at?: string;
  _cached?: boolean;
}

export const sentimentApi = {
  async getSentiment(
    symbol: string,
    days: number = 90,
    force: boolean = false,
  ): Promise<SentimentResponse> {
    const response = await apiClient.get<SentimentResponse>(
      '/api/v1/stocks/sentiment',
      { params: { symbol, days, force }, timeout: 45000 },
    );
    return response.data;
  },
};

// ---------------------------------------------------------------------------
// Research Reports
// ---------------------------------------------------------------------------

export interface ProfitForecast {
  year: string;
  eps: number;
  pe: number | null;
}

export interface ResearchReportItem {
  title: string;
  org: string;
  rating: string;
  industry: string;
  publish_date: string | null;
  url: string;
  profit_forecasts: ProfitForecast[];
  monthly_report_count: number | null;
}

export interface ResearchReportResponse {
  symbol: string;
  days: number;
  items: ResearchReportItem[];
  analysis?: Record<string, unknown>;
  errors?: string[];
  _fetched_at?: string;
  _cached?: boolean;
}

export const researchReportApi = {
  async getResearchReports(
    symbol: string,
    days: number = 1095,
    force: boolean = false,
  ): Promise<ResearchReportResponse> {
    const response = await apiClient.get<ResearchReportResponse>(
      '/api/v1/stocks/research-report',
      { params: { symbol, days, force }, timeout: 45000 },
    );
    return response.data;
  },
};

// ---------------------------------------------------------------------------
// Social Media Sentiment
// ---------------------------------------------------------------------------

export interface SocialSentimentItem {
  title: string;
  content: string;
  source: string;
  url: string;
  read_count: number;
  reply_count: number;
  sentiment_score: number;
  label: 'positive' | 'negative' | 'neutral';
  publish_time: string | null;
}

export interface SocialScoreTrendItem {
  date: string;
  score: number | null;
  close: number | null;
}

export interface SocialDailyTrendItem {
  date: string;
  total: number;
  positive: number;
  negative: number;
  neutral: number;
  read_total?: number;
  reply_total?: number;
  score?: number;
}

export interface SocialSentimentResponse {
  symbol: string;
  days: number;
  overall_score: number;
  total_discussion: number;
  total_read: number;
  total_reply: number;
  positive_count: number;
  negative_count: number;
  neutral_count: number;
  diagnose_score: number | null;
  score_trend: SocialScoreTrendItem[];
  daily_trend: SocialDailyTrendItem[];
  items: SocialSentimentItem[];
  analysis?: Record<string, unknown>;
  errors?: string[];
  _fetched_at?: string;
  _cached?: boolean;
}

export const socialSentimentApi = {
  async getSocialSentiment(
    symbol: string,
    days: number = 90,
    force: boolean = false,
  ): Promise<SocialSentimentResponse> {
    const response = await apiClient.get<SocialSentimentResponse>(
      '/api/v1/stocks/social-sentiment',
      { params: { symbol, days, force }, timeout: 45000 },
    );
    return response.data;
  },
};

export const financialStatementsApi = {
  async getStatements(
    symbol: string,
    periods: number = 12,
    force: boolean = false,
  ): Promise<FinancialStatementsResponse> {
    const response = await apiClient.get<FinancialStatementsResponse>(
      '/api/v1/stocks/financials/statements',
      { params: { symbol, periods, force }, timeout: 60000 },
    );
    return response.data;
  },
};
