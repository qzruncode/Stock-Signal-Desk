import { useCallback, useEffect, useState } from 'react';
import {
  RSS_SUBSCRIPTIONS_UPDATED_EVENT,
  fetchRssSubscriptions,
} from '../utils/rssSubscriptions';
import type { RssSubscription } from '../api/rss';

export interface UseRssSubscriptionsResult {
  subscriptions: RssSubscription[];
  loading: boolean;
  /** True while a mutation-triggered refetch is in flight (delete/subscribe/reorder). */
  mutating: boolean;
  reload: () => Promise<void>;
}

/**
 * Owns RSS subscriptions state, synced from the backend (source of truth).
 * Refetches whenever any component dispatches RSS_SUBSCRIPTIONS_UPDATED_EVENT
 * (emitted by every subscription mutation).
 *
 * `loading` covers the initial mount fetch only; `mutating` toggles during the
 * silent refetch a mutation kicks off, so the UI can show feedback without the
 * whole list disappearing.
 */
export function useRssSubscriptions(): UseRssSubscriptionsResult {
  const [subscriptions, setSubscriptions] = useState<RssSubscription[]>([]);
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);

  const reload = useCallback(async () => {
    setMutating(true);
    try {
      const next = await fetchRssSubscriptions();
      setSubscriptions(next);
    } finally {
      setMutating(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    const init = async () => {
      try {
        const next = await fetchRssSubscriptions();
        if (!cancelled) setSubscriptions(next);
      } catch {
        if (!cancelled) setSubscriptions([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void init();

    const handleUpdate = () => { void reload(); };
    window.addEventListener(RSS_SUBSCRIPTIONS_UPDATED_EVENT, handleUpdate);
    return () => {
      cancelled = true;
      window.removeEventListener(RSS_SUBSCRIPTIONS_UPDATED_EVENT, handleUpdate);
    };
  }, [reload]);

  return { subscriptions, loading, mutating, reload };
}

export default useRssSubscriptions;
