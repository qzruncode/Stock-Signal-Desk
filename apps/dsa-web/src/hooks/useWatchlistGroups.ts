import { useCallback, useEffect, useState } from 'react';
import {
  WATCHLIST_GROUPS_UPDATED_EVENT,
  fetchWatchlistGroups,
  migrateLegacyWatchlistGroups,
  type WatchlistGroup,
} from '../utils/watchlistGroups';

export interface UseWatchlistGroupsResult {
  groups: WatchlistGroup[];
  loading: boolean;
  reload: () => Promise<void>;
}

/**
 * Owns the custom watchlist groups state, synced from the backend.
 *
 * - Runs the one-time localStorage → DB migration on first mount.
 * - Refetches whenever any component dispatches WATCHLIST_GROUPS_UPDATED_EVENT
 *   (the single signal emitted by every group mutation), keeping the server as
 *   the source of truth.
 */
export function useWatchlistGroups(): UseWatchlistGroupsResult {
  const [groups, setGroups] = useState<WatchlistGroup[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    const next = await fetchWatchlistGroups();
    setGroups(next);
  }, []);

  useEffect(() => {
    let cancelled = false;

    const init = async () => {
      try {
        await migrateLegacyWatchlistGroups();
        const next = await fetchWatchlistGroups();
        if (!cancelled) setGroups(next);
      } catch {
        if (!cancelled) setGroups([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void init();

    const handleUpdate = () => { void reload(); };
    window.addEventListener(WATCHLIST_GROUPS_UPDATED_EVENT, handleUpdate);
    return () => {
      cancelled = true;
      window.removeEventListener(WATCHLIST_GROUPS_UPDATED_EVENT, handleUpdate);
    };
  }, [reload]);

  return { groups, loading, reload };
}
