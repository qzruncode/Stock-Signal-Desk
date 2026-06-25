import apiClient from './index';

export interface RssItem {
  title: string;
  link: string;
  summary: string;
  published: string | null;
  author: string;
  tags: string[];
}

export interface RssFeedResponse {
  source: string;
  feed_title: string;
  feed_link: string;
  items: RssItem[];
  errors: string[];
  _fetched_at: string;
  _cached: boolean;
}

export interface RssSourceOption {
  id: string;
  label: string;
  group: string;
  requires_stock: boolean;
  stock_placeholder: string;
  requires_type: boolean;
  type_options: { value: string; label: string }[];
  default_type: string;
  requires_category: boolean;
  category_options: { value: string; label: string }[];
  default_category: string;
  requires_keyword: boolean;
  keyword_placeholder: string;
  requires_uid: boolean;
  uid_placeholder: string;
}

export interface RssSourcesResponse {
  sources: RssSourceOption[];
}

export const rssApi = {
  getFeeds(params: {
    source: string;
    stock_code?: string;
    type?: string;
    category?: string;
    keyword?: string;
    uid?: string;
    limit?: number;
    force?: boolean;
  }, signal?: AbortSignal): Promise<RssFeedResponse> {
    return apiClient
      .get('/api/v1/rss/feeds', { params, timeout: 30000, signal })
      .then((r) => r.data);
  },

  getSources(): Promise<RssSourcesResponse> {
    return apiClient
      .get('/api/v1/rss/sources')
      .then((r) => r.data);
  },
};
