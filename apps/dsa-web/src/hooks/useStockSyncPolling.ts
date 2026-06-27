import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { stocksApi, type SyncStatusResponse } from '../api/stocks';

export interface UseStockSyncPollingResult {
  syncStatus: SyncStatusResponse | null;
  isSyncing: boolean;
  syncError: string | null;
  pollRef: RefObject<ReturnType<typeof setInterval> | null>;
  handleSync: () => Promise<void>;
  startPolling: () => void;
  isSyncingActive: boolean;
}

interface UseStockSyncPollingOptions {
  loadWatchlist: () => Promise<void>;
  loadStockList: (page: number, search: string, market: string, append: boolean) => Promise<void>;
  stockSearch: string;
  stockMarket: string;
}

const POLL_INTERVAL_MS = 2000;

/**
 * Owns the market-stock sync status, polling lifecycle, and the filter refs
 * that the polling interval reads to avoid stale closures.
 *
 * - Exposes `syncStatus` / `isSyncing` / `syncError` for the page to render.
 * - `startPolling` drives a 2000ms `setInterval` that refreshes sync status and,
 *   once a sync reaches a terminal state, reloads the stock list (and watchlist)
 *   using the latest filter values held in refs.
 * - `handleSync` kicks off a fresh sync, tears down any in-flight polling, and
 *   restarts polling only when the backend reports an active state.
 * - On unmount the interval is cleared so polling never leaks across navigations.
 *
 * The page still owns stock-list and watchlist state; this hook only takes the
 * sync/polling slice and reports back through the returned state and callbacks.
 */
export function useStockSyncPolling({
  loadWatchlist,
  loadStockList,
  stockSearch,
  stockMarket,
}: UseStockSyncPollingOptions): UseStockSyncPollingResult {
  const [syncStatus, setSyncStatus] = useState<SyncStatusResponse | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Refs for latest filter values (read by the polling interval to avoid stale closures).
  const stockSearchRef = useRef(stockSearch);
  const stockMarketRef = useRef(stockMarket);

  useEffect(() => { stockSearchRef.current = stockSearch; }, [stockSearch]);
  useEffect(() => { stockMarketRef.current = stockMarket; }, [stockMarket]);

  const loadSyncStatus = useCallback(async (): Promise<SyncStatusResponse | null> => {
    try {
      const status = await stocksApi.syncStatus();
      setSyncStatus(status);
      return status;
    } catch {
      return null;
    }
  }, []);

  const startPolling = useCallback(() => {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      try {
        const s = await stocksApi.syncStatus();
        setSyncStatus(s);
        if (s.status === 'success' || s.status === 'failed') {
          if (pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
          if (s.status === 'success') {
            void loadStockList(1, stockSearchRef.current, stockMarketRef.current, false);
            void loadWatchlist();
          }
        }
      } catch {
        /* ignore transient polling errors */
      }
    }, POLL_INTERVAL_MS);
  }, [loadStockList, loadWatchlist]);

  // On mount, fetch the current sync status and begin polling if a sync is active.
  // On unmount, tear down the interval so polling never outlives the component.
  useEffect(() => {
    void loadSyncStatus().then((status) => {
      if (status?.status === 'running' || status?.status === 'syncing_kline') {
        startPolling();
      }
    });
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [loadSyncStatus, startPolling]);

  const handleSync = useCallback(async () => {
    if (isSyncing) return;
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setIsSyncing(true);
    setSyncError(null);
    try {
      const result = await stocksApi.sync();
      if (result.success) {
        const status = await loadSyncStatus();
        if (status?.status === 'running' || status?.status === 'syncing_kline') {
          startPolling();
        }
      }
    } catch (err: unknown) {
      setSyncError(err instanceof Error ? err.message : '同步启动失败');
    } finally {
      setIsSyncing(false);
    }
  }, [isSyncing, loadSyncStatus, startPolling]);

  const isSyncingActive =
    isSyncing || syncStatus?.status === 'running' || syncStatus?.status === 'syncing_kline';

  return {
    syncStatus,
    isSyncing,
    syncError,
    pollRef,
    handleSync,
    startPolling,
    isSyncingActive,
  };
}
