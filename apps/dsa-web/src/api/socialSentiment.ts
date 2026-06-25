import apiClient from './index';

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
