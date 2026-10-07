import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { rssApi, type RssRouteDescriptor } from '../api/rss';
import { isHiddenFromExplore } from '../utils/rssRoute';

// The backend /namespaces endpoint already applies the explore-visibility
// filter (api/v1/endpoints/_rss_filter.py), so the curated ~47-route catalog
// arrives pre-filtered. We keep a defensive client-side pass as a fallback in
// case a future backend change ships unfiltered routes — it is a no-op when
// the backend filter is active.

export interface UseRssNamespacesResult {
  loading: boolean;
  error: string | null;
  filtered: RssRouteDescriptor[];
}

// Module-level cache: the 3.3MB blob is fetched once per session; navigating
// away and back does not refetch (the backend's own 6h cache also guards this).
let cachedRoutes: RssRouteDescriptor[] | null = null;

export function useRssNamespaces(): UseRssNamespacesResult {
  const [routes, setRoutes] = useState<RssRouteDescriptor[]>(cachedRoutes ?? []);
  const [loading, setLoading] = useState(!cachedRoutes);
  const [error, setError] = useState<string | null>(null);
  const seqRef = useRef(0);

  const load = useCallback(async () => {
    if (cachedRoutes) {
      setRoutes(cachedRoutes);
      setLoading(false);
      return;
    }
    const seq = ++seqRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await rssApi.getNamespaces(false);
      if (seq !== seqRef.current) return; // stale
      cachedRoutes = res.routes;
      setRoutes(res.routes);
      if (res._error) setError(res._error);
    } catch (err: unknown) {
      if (seq !== seqRef.current) return;
      const msg = (err as Error)?.message || '获取 RSSHub 路由失败';
      setError(msg);
    } finally {
      if (seq === seqRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    return routes.filter((r) => !isHiddenFromExplore(r));
  }, [routes]);

  return {
    loading,
    error,
    filtered,
  };
}
