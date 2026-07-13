import { useCallback, useEffect, useRef, useState } from 'react';
import { rssApi, type FeedSpec, type RssFeedBySpecResponse } from '../api/rss';

export interface UseRssFeedsResult {
  feedData: RssFeedBySpecResponse | null;
  loading: boolean;
  error: string | null;
  fetchFeeds: (force?: boolean) => Promise<void>;
}

// ── Session-level feed cache ──────────────────────────────────────────
// Switching back to a previously viewed route used to re-trigger a POST and
// show a loading spinner every time — the backend cache only made each request
// fast, it couldn't stop the round-trip/flash. This Map keys on the stable
// spec signature (route_path + params + options, mirroring the backend's
// _rss_cache_key_generic minus the hour bucket) and remembers successful
// results for the session, so revisits are instant. force=true bypasses it.
//
// Mirrors the module-level cachedRoutes pattern in useRssNamespaces.
const feedCache = new Map<string, RssFeedBySpecResponse>();

/** Stable signature for a FeedSpec — must stay in sync with backend key parts. */
function specKey(spec: FeedSpec): string {
  const params = spec.params ?? {};
  const options = spec.options ?? {};
  // Object.entries avoids indexing the fixed-key RssFeedOptions interface with
  // an arbitrary string (TS7053). Sort for determinism.
  const p = JSON.stringify(Object.entries(params).sort());
  const o = JSON.stringify(Object.entries(options).sort());
  return `${spec.route_path}|${p}|${o}`;
}

/** Only successful results with items are worth remembering. */
function isCacheable(data: RssFeedBySpecResponse | null): data is RssFeedBySpecResponse {
  return Boolean(data && data.items && data.items.length > 0);
}

/**
 * Fetch a feed by generic FeedSpec (shared by subscribed feeds and ad-hoc explore).
 * - Aborts in-flight requests on new fetch / unmount.
 * - Auto-fetches when `spec` changes (and enabled), so the page only owns the spec.
 * - Serves session-cached results instantly on revisits (no spinner flash); the
 *   refresh / force-refresh buttons still hit the network.
 */
export function useRssFeeds(
  spec: FeedSpec | null,
  enabled = true,
): UseRssFeedsResult {
  const [feedData, setFeedData] = useState<RssFeedBySpecResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Monotonic request sequence: each fetchFeeds() bumps it. A request is only
  // allowed to mutate state while it is still the latest one — so when a slow
  // request is aborted by a newer one, its finally() does NOT clear the newer
  // request's loading flag. Without this, a slow route (e.g. /followin/news on
  // first uncached hit) shows no loading because the aborted request's
  // setLoading(false) runs after the new request's setLoading(true).
  const seqRef = useRef(0);
  const specRef = useRef<FeedSpec | null>(spec);

  // Keep specRef in sync inside an effect (never during render) so the stable
  // fetchFeeds callback can read the latest spec without it in its deps.
  useEffect(() => {
    specRef.current = spec;
  }, [spec]);

  const fetchFeeds = useCallback(async (force = false) => {
    const current = specRef.current;
    if (!current) {
      setFeedData(null);
      return;
    }
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const seq = ++seqRef.current;
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
      if (seq !== seqRef.current) return; // superseded by a newer request
      setFeedData(result);
      // Remember successful results so revisits are instant; force-refresh
      // overwrites the entry with the freshest data.
      if (isCacheable(result)) {
        feedCache.set(specKey(current), result);
      }
      if (result.errors?.length) {
        setError(result.errors.join('; '));
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      if ((err as { code?: string })?.code === 'ERR_CANCELED') return;
      if (seq !== seqRef.current) return; // superseded — don't touch state
      const msg =
        (err as { response?: { data?: { detail?: { message?: string } } } })
          ?.response?.data?.detail?.message ||
        (err as Error).message ||
        '获取 RSS 数据失败';
      setError(msg);
    } finally {
      // Only the latest request may clear loading; an aborted superseded
      // request must leave the active request's loading flag intact.
      if (seq === seqRef.current) setLoading(false);
    }
  }, []);

  // Auto-fetch when spec changes (and enabled). Serve cached result instantly
  // on revisits so switching routes doesn't flash a spinner every time.
  useEffect(() => {
    if (!enabled || !spec) {
      setFeedData(null);
      setLoading(false);
      return;
    }
    const cached = feedCache.get(specKey(spec));
    if (cached) {
      // Instant hit — show remembered data without a network round-trip.
      setFeedData(cached);
      setError(null);
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
