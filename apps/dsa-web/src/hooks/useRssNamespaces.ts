import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { rssApi, type RssRouteDescriptor } from '../api/rss';
import { useDebouncedValue } from './useDebouncedValue';

export interface UseRssNamespacesResult {
  routes: RssRouteDescriptor[];
  categories: string[];
  loading: boolean;
  error: string | null;
  stale: boolean;
  search: string;
  setSearch: (v: string) => void;
  category: string;
  setCategory: (v: string) => void;
  filtered: RssRouteDescriptor[];
  reload: () => Promise<void>;
}

// Module-level cache: the 3.3MB blob is fetched once per session; navigating
// away and back does not refetch (the backend's own 6h cache also guards this).
let cachedRoutes: RssRouteDescriptor[] | null = null;
let cachedCategories: string[] = [];

function matchesKeyword(route: RssRouteDescriptor, kw: string): boolean {
  if (!kw) return true;
  const hay = `${route.name} ${route.namespace_name} ${route.namespace} ${route.route_path} ${route.description}`.toLowerCase();
  return hay.includes(kw.toLowerCase());
}

export function useRssNamespaces(): UseRssNamespacesResult {
  const [routes, setRoutes] = useState<RssRouteDescriptor[]>(cachedRoutes ?? []);
  const [categories, setCategories] = useState<string[]>(cachedCategories);
  const [loading, setLoading] = useState(!cachedRoutes);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const debouncedSearch = useDebouncedValue(search, 300);
  const seqRef = useRef(0);

  const load = useCallback(async (force = false) => {
    if (!force && cachedRoutes) {
      setRoutes(cachedRoutes);
      setCategories(cachedCategories);
      setLoading(false);
      return;
    }
    const seq = ++seqRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await rssApi.getNamespaces(force);
      if (seq !== seqRef.current) return; // stale
      cachedRoutes = res.routes;
      cachedCategories = res.categories;
      setRoutes(res.routes);
      setCategories(res.categories);
      setStale(res._stale);
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
    void load(false);
  }, [load]);

  const reload = useCallback(async () => {
    await load(true);
  }, [load]);

  const filtered = useMemo(() => {
    let list = routes;
    if (category) {
      list = list.filter((r) => r.categories.includes(category));
    }
    if (debouncedSearch.trim()) {
      list = list.filter((r) => matchesKeyword(r, debouncedSearch.trim()));
    }
    return list;
  }, [routes, category, debouncedSearch]);

  return {
    routes,
    categories,
    loading,
    error,
    stale,
    search,
    setSearch,
    category,
    setCategory,
    filtered,
    reload,
  };
}

export default useRssNamespaces;
