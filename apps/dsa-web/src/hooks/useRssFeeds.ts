import { useCallback, useEffect, useRef, useState } from 'react';
import { rssApi, type FeedSpec, type RssFeedBySpecResponse } from '../api/rss';

export interface UseRssFeedsResult {
  feedData: RssFeedBySpecResponse | null;
  loading: boolean;
  error: string | null;
  fetchFeeds: (force?: boolean) => Promise<void>;
}

/**
 * Fetch a feed by generic FeedSpec (shared by subscribed feeds and ad-hoc explore).
 * - Aborts in-flight requests on new fetch / unmount.
 * - Auto-fetches when `spec` changes (and enabled), so the page only owns the spec.
 */
export function useRssFeeds(
  spec: FeedSpec | null,
  enabled = true,
): UseRssFeedsResult {
  const [feedData, setFeedData] = useState<RssFeedBySpecResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const specRef = useRef<FeedSpec | null>(spec);
  specRef.current = spec;

  const fetchFeeds = useCallback(async (force = false) => {
    const current = specRef.current;
    if (!current) {
      setFeedData(null);
      return;
    }
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    setError(null);
    try {
      const result = await rssApi.getFeedsBySpec(
        {
          route_path: current.route_path,
          params: current.params,
          options: current.options,
          namespace: current.namespace,
          force,
        },
        ctrl.signal,
      );
      setFeedData(result);
      if (result.errors?.length) {
        setError(result.errors.join('; '));
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      if ((err as { code?: string })?.code === 'ERR_CANCELED') return;
      const msg =
        (err as { response?: { data?: { detail?: { message?: string } } } })
          ?.response?.data?.detail?.message ||
        (err as Error).message ||
        '获取 RSS 数据失败';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  // Auto-fetch when spec changes (and enabled).
  useEffect(() => {
    if (!enabled || !spec) {
      setFeedData(null);
      setLoading(false);
      return;
    }
    setFeedData(null);
    void fetchFeeds(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec, enabled]);

  // Abort in-flight requests on unmount.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  return { feedData, loading, error, fetchFeeds };
}

export default useRssFeeds;
