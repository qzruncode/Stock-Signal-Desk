import { useCallback, useEffect, useRef, useState } from 'react';
import { rssApi, type RssFeedResponse, type RssSourceOption } from '../api/rss';

export interface UseRssFeedsParams {
  source: string;
  stockCode: string;
  keyword: string;
  uid: string;
  subType: string;
  category: string;
  currentSource: RssSourceOption | undefined;
  sourcesLength: number;
}

export interface UseRssFeedsResult {
  feedData: RssFeedResponse | null;
  loading: boolean;
  error: string | null;
  fetchFeeds: () => Promise<void>;
}

export function useRssFeeds({
  source,
  stockCode,
  keyword,
  uid,
  subType,
  category,
  currentSource,
  sourcesLength,
}: UseRssFeedsParams): UseRssFeedsResult {
  const [feedData, setFeedData] = useState<RssFeedResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const fetchFeeds = useCallback(async () => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, unknown> = { source, limit: 30 };
      if (currentSource?.requires_stock && stockCode) params.stock_code = stockCode;
      if (currentSource?.requires_keyword && keyword) params.keyword = keyword;
      if (currentSource?.requires_uid && uid) params.uid = uid;
      if (currentSource?.requires_type && subType) params.type = subType;
      if (currentSource?.requires_category && category) params.category = category;
      const result = await rssApi.getFeeds(
        params as Parameters<typeof rssApi.getFeeds>[0],
        ctrl.signal,
      );
      setFeedData(result);
      if (result.errors?.length) {
        setError(result.errors.join('; '));
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      const msg =
        (err as { response?: { data?: { detail?: { message?: string } } } })
          ?.response?.data?.detail?.message ||
        (err as Error).message ||
        '获取 RSS 数据失败';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [source, stockCode, keyword, uid, subType, category, currentSource]);

  // Auto-load when sources are ready
  useEffect(() => {
    if (sourcesLength > 0) {
      void fetchFeeds();
    }
  }, [fetchFeeds, sourcesLength]);

  // Abort in-flight requests on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  return { feedData, loading, error, fetchFeeds };
}

export default useRssFeeds;