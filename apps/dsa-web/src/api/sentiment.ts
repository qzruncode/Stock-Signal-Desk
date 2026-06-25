import apiClient from './index';

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
