import apiClient from './index';

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
