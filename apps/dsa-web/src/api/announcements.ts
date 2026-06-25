import apiClient from './index';

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
