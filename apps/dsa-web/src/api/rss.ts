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

/** 格隆汇主题（供 /gelonghui/subject/:id 路由选参）。 */
export interface GelonghuiSubject {
  subjectId: number;
  name: string;
  followCount: number;
  summary: string;
  link: string;
}

export interface GelonghuiSubjectsResponse {
  subjects: GelonghuiSubject[];
  total: number;
  _fetched_at?: string | null;
  _cached?: boolean;
  _stale?: boolean;
  _error?: string | null;
}

/** 南华期货研报二级分类（供 /nanhua/report/:type1/:type2 路由选参）。 */
export interface NanhuaReportType2 {
  type: string;
  name: string;
}

/** 南华期货研报一级分类（含其下的二级分类列表）。 */
export interface NanhuaReportType1 {
  type: string;
  name: string;
  children: NanhuaReportType2[];
}

export interface NanhuaTreeResponse {
  types: NanhuaReportType1[];
  total: number;
  _fetched_at?: string | null;
  _cached?: boolean;
  _stale?: boolean;
  _error?: string | null;
}

/** 中指指数报告一级分类（供 /cih-index/report/list/:report? 路由选参）。 */
export interface CihIndexCategory {
  classId: string;
  className: string;
}

export interface CihIndexCategoriesResponse {
  categories: CihIndexCategory[];
  total: number;
  _fetched_at?: string | null;
  _cached?: boolean;
  _stale?: boolean;
  _error?: string | null;
}

/** 财联社话题（供 /cls/subject/:id? 路由选参）。 */
export interface ClsSubject {
  subjectId: string;
  name: string;
  attention_num: number;
  link: string;
}

export interface ClsSubjectsResponse {
  subjects: ClsSubject[];
  total: number;
  _fetched_at?: string | null;
  _cached?: boolean;
  _stale?: boolean;
  _error?: string | null;
}

/** 富途牛牛话题（供 /futunn/topic/:id 路由选参）。 */
export interface FutunnTopic {
  topicId: string;
  title: string;
  detail: string;
  subscribed: number;
  timestamp: number;
  link: string;
}

export interface FutunnTopicsResponse {
  topics: FutunnTopic[];
  total: number;
  _fetched_at?: string | null;
  _cached?: boolean;
  _stale?: boolean;
  _error?: string | null;
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

  // 格隆汇主题列表（供 /gelonghui/subject/:id 路由选参）
  getGelonghuiSubjects(
    params: { keyword?: string; force?: boolean },
    signal?: AbortSignal,
  ): Promise<GelonghuiSubjectsResponse> {
    return apiClient
      .get('/api/v1/rss/gelonghui/subjects', { params, timeout: 15000, signal })
      .then((r) => r.data);
  },

  // 南华期货研报分类树（供 /nanhua/report/:type1/:type2 路由级联选参）
  getNanhuaReportTypes(
    params: { force?: boolean } = {},
    signal?: AbortSignal,
  ): Promise<NanhuaTreeResponse> {
    return apiClient
      .get('/api/v1/rss/nanhua/report-types', { params, timeout: 15000, signal })
      .then((r) => r.data);
  },

  // 中指指数报告一级分类（供 /cih-index/report/list/:report? 路由选参）
  getCihIndexCategories(
    params: { force?: boolean } = {},
    signal?: AbortSignal,
  ): Promise<CihIndexCategoriesResponse> {
    return apiClient
      .get('/api/v1/rss/cih-index/report-categories', { params, timeout: 15000, signal })
      .then((r) => r.data);
  },

  // 财联社话题列表（供 /cls/subject/:id? 路由选参）
  getClsSubjects(
    params: { keyword?: string; force?: boolean } = {},
    signal?: AbortSignal,
  ): Promise<ClsSubjectsResponse> {
    return apiClient
      .get('/api/v1/rss/cls/subjects', { params, timeout: 15000, signal })
      .then((r) => r.data);
  },

  // 富途牛牛话题列表（供 /futunn/topic/:id 路由选参）
  getFutunnTopics(
    params: { keyword?: string; force?: boolean } = {},
    signal?: AbortSignal,
  ): Promise<FutunnTopicsResponse> {
    return apiClient
      .get('/api/v1/rss/futunn/topics', { params, timeout: 15000, signal })
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
    item: Pick<
      RssItem,
      'id' | 'title' | 'link' | 'content_html' | 'summary' | 'image'
      | 'published' | 'author' | 'tags' | 'attachments'
    >,
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
        // The list item's already-rendered body lets the backend fall back to
        // it when the fulltext re-fetch is poorer (e.g. cih-index image reports)
        // or comes back empty (e.g. /eeo/kuaixun — RSSHub applies filter_title
        // after limit truncation, so the target can be missing from the batch).
        // published/author/tags/attachments are forwarded so the synthesized
        // fallback keeps everything the detail view renders.
        content_html: item.content_html ?? '',
        summary: item.summary ?? '',
        image: item.image ?? '',
        published: item.published ?? '',
        author: item.author ?? '',
        tags: item.tags ?? [],
        attachments: item.attachments ?? [],
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
