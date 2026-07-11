import apiClient from './index';

// ── Feed item（富字段：图/音频/全文，来自 JSON Feed 取数）─────────────────

export interface RssAttachment {
  url: string;
  mime_type: string;
  title?: string;
  size_in_bytes?: number | null;
  duration_in_seconds?: number | null;
}

export interface RssItem {
  id: string;
  title: string;
  link: string;
  summary: string;
  published: string | null;
  author: string;
  tags: string[];
  image?: string;
  content_html?: string;
  attachments?: RssAttachment[];
}

// ── 动态发现 + 通用 FeedSpec 类型 ───────────────────────────────────────

/** RSSHub 通用查询参数（仅发 truthy）。 */
export interface RssFeedOptions {
  limit?: number;
  filter?: string;
  filter_title?: string;
  filter_description?: string;
  filter_author?: string;
  filter_category?: string;
  filterout?: string;
  filterout_title?: string;
  filter_case_sensitive?: boolean;
  filter_time?: number;
  sorted?: boolean;
  mode?: 'fulltext';
  opencc?: string;
  brief?: number;
  format?: 'rss' | 'atom' | 'json' | 'rss3';
  tgiv?: string;
  scihub?: string;
}

/** 一条 RSSHub 路由描述（来自发现的扁平结构）。 */
export interface RssRouteDescriptor {
  namespace: string;
  namespace_name: string;
  route_path: string;
  name: string;
  url: string;
  example: string;
  categories: string[];
  description: string;
  parameters: Record<string, unknown>;
  features: {
    requireConfig?: boolean;
    requirePuppeteer?: boolean;
    antiCrawler?: boolean;
    supportRadar?: boolean;
    supportBT?: boolean;
    supportPodcast?: boolean;
    supportScihub?: boolean;
    nsfw?: boolean;
  };
  maintainers: string[];
}

export interface RssNamespacesResponse {
  routes: RssRouteDescriptor[];
  namespace_count: number;
  route_count: number;
  categories: string[];
  _fetched_at: string | null;
  _cached: boolean;
  _stale: boolean;
  _error: string | null;
}

export interface RssNamespaceDetail {
  namespace: string;
  name: string;
  routes: Record<string, unknown>;
  _fetched_at: string | null;
  _cached: boolean;
  _stale: boolean;
  _error: string | null;
}

export interface RssCategoriesResponse {
  categories: string[];
}

/** 通用 FeedSpec —— 订阅与临时浏览共用。 */
export interface FeedSpec {
  route_path: string;
  namespace: string;
  params: Record<string, string>;
  options: RssFeedOptions;
}

export interface RssFeedBySpecResponse {
  route_path: string;
  params: Record<string, string>;
  options: RssFeedOptions;
  feed_title: string;
  feed_link: string;
  items: RssItem[];
  errors: string[];
  _fetched_at: string;
  _cached: boolean;
}

/** 一条持久化订阅。 */
export interface RssSubscription {
  id: string;
  title: string;
  namespace: string;
  routePath: string;
  params: Record<string, string>;
  options: RssFeedOptions;
  sortOrder: number;
}

export interface RssSubscriptionsResponse {
  subscriptions: RssSubscription[];
}

export interface RssFeaturedResponse {
  routes: (RssRouteDescriptor & { source_id?: string })[];
}

export type RssFeedFormat = 'rss' | 'atom' | 'json' | 'rss3';

export interface HtmlTransformParams {
  url: string;
  title?: string;
  item?: string;
  itemTitle?: string;
  itemLink?: string;
  itemDesc?: string;
  itemPubDate?: string;
  itemContent?: string;
  encoding?: string;
}

export interface HtmlTransformRequest extends HtmlTransformParams {
  limit?: number;
  options?: RssFeedOptions;
}

export interface HtmlTransformResponse {
  route_path: string;
  namespace: string;
  feed_title: string;
  feed_link: string;
  items: RssItem[];
  errors: string[];
  _fetched_at: string;
  _cached: boolean;
  persist_params: Record<string, string>;
}

/** 测试实例 XUEQIU_COOKIES 是否生效的结果。 */
export interface RssCookieTestResult {
  configured: boolean;
  verified: boolean;
  item_count: number;
  message: string;
}

// ── API ────────────────────────────────────────────────────────────────

export const rssApi = {
  // 路由发现
  getNamespaces(force = false, signal?: AbortSignal): Promise<RssNamespacesResponse> {
    return apiClient
      .get('/api/v1/rss/namespaces', { params: { force }, timeout: 30000, signal })
      .then((r) => r.data);
  },

  getNamespaceDetail(namespace: string, signal?: AbortSignal): Promise<RssNamespaceDetail> {
    return apiClient
      .get(`/api/v1/rss/namespaces/${namespace}`, { timeout: 15000, signal })
      .then((r) => r.data);
  },

  getCategories(force = false): Promise<RssCategoriesResponse> {
    return apiClient
      .get('/api/v1/rss/categories', { params: { force } })
      .then((r) => r.data);
  },

  getFeatured(): Promise<RssFeaturedResponse> {
    return apiClient
      .get('/api/v1/rss/featured')
      .then((r) => r.data);
  },

  // 新：通用 Feed 取数
  getFeedsBySpec(
    spec: { route_path: string; params: Record<string, string>; options: RssFeedOptions; namespace?: string; limit?: number; force?: boolean },
    signal?: AbortSignal,
  ): Promise<RssFeedBySpecResponse> {
    return apiClient
      .post('/api/v1/rss/feeds', spec, { timeout: 60000, signal })
      .then((r) => r.data);
  },

  getFeedItemDetail(
    spec: FeedSpec,
    item: Pick<RssItem, 'id' | 'title' | 'link'>,
  ): Promise<RssItem> {
    return apiClient
      .post('/api/v1/rss/feeds/item', {
        route_path: spec.route_path,
        params: spec.params,
        options: spec.options,
        namespace: spec.namespace,
        item_id: item.id,
        title: item.title,
        link: item.link,
      }, { timeout: 60000 })
      .then((r) => r.data);
  },

  // 新：订阅 CRUD
  listSubscriptions(signal?: AbortSignal): Promise<RssSubscriptionsResponse> {
    return apiClient
      .get('/api/v1/rss/subscriptions', { signal })
      .then((r) => r.data);
  },

  upsertSubscription(body: {
    title: string;
    namespace: string;
    route_path: string;
    params?: Record<string, string>;
    options?: RssFeedOptions;
  }): Promise<RssSubscription> {
    return apiClient
      .post('/api/v1/rss/subscriptions', body)
      .then((r) => r.data);
  },

  updateSubscription(id: string, patch: {
    title?: string;
    namespace?: string;
    route_path?: string;
    params?: Record<string, string>;
    options?: RssFeedOptions;
  }): Promise<RssSubscription> {
    return apiClient
      .patch(`/api/v1/rss/subscriptions/${id}`, patch)
      .then((r) => r.data);
  },

  deleteSubscription(id: string): Promise<{ deleted: boolean }> {
    return apiClient
      .delete(`/api/v1/rss/subscriptions/${id}`)
      .then((r) => r.data);
  },

  reorderSubscriptions(orderedIds: string[]): Promise<{ reordered: boolean }> {
    return apiClient
      .patch('/api/v1/rss/subscriptions/reorder', { ordered_ids: orderedIds })
      .then((r) => r.data);
  },

  // 原始 feed 透传（多格式下载）—— 返回 Blob
  getRawFeed(body: {
    route_path: string;
    params: Record<string, string>;
    options?: RssFeedOptions;
    namespace?: string;
    format: RssFeedFormat;
    limit?: number;
  }): Promise<Blob> {
    return apiClient
      .post('/api/v1/rss/feeds/raw', body, { responseType: 'blob', timeout: 30000 })
      .then((r) => r.data as Blob);
  },

  // HTML→RSS 万能转换器
  transformHtml(body: HtmlTransformRequest, signal?: AbortSignal): Promise<HtmlTransformResponse> {
    return apiClient
      .post('/api/v1/rss/transform/html', body, { timeout: 30000, signal })
      .then((r) => r.data);
  },

  // 测试实例配置的雪球 Cookie 是否生效（实际请求一次 timeline）
  testXueqiuCookie(): Promise<RssCookieTestResult> {
    return apiClient
      .post('/api/v1/rss/instance/cookies/test', {}, { timeout: 45000 })
      .then((r) => r.data);
  },
};
