import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { stocksApi, type SyncStatusResponse } from '../api/stocks';

export interface UseStockSyncPollingResult {
  listSyncStatus: SyncStatusResponse | null;
  klineSyncStatus: SyncStatusResponse | null;
  syncError: string | null;
  pollRef: RefObject<ReturnType<typeof setInterval> | null>;
  handleSyncList: () => Promise<void>;
  handleSyncKline: () => Promise<void>;
  isListSyncingActive: boolean;
  isKlineSyncingActive: boolean;
}

interface UseStockSyncPollingOptions {
  loadWatchlist: () => Promise<void>;
  loadStockList: (page: number, search: string, market: string, append: boolean) => Promise<void>;
  stockSearch: string;
  stockMarket: string;
}

const POLL_INTERVAL_MS = 2000;

const isActiveStatus = (status?: SyncStatusResponse | null) =>
  status?.status === 'running' || status?.status === 'syncing_kline';

export function useStockSyncPolling({
  loadWatchlist,
  loadStockList,
  stockSearch,
  stockMarket,
}: UseStockSyncPollingOptions): UseStockSyncPollingResult {
  const [listSyncStatus, setListSyncStatus] = useState<SyncStatusResponse | null>(null);
  const [klineSyncStatus, setKlineSyncStatus] = useState<SyncStatusResponse | null>(null);
  const [isStartingList, setIsStartingList] = useState(false);
  const [isStartingKline, setIsStartingKline] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stockSearchRef = useRef(stockSearch);
  const stockMarketRef = useRef(stockMarket);

  useEffect(() => { stockSearchRef.current = stockSearch; }, [stockSearch]);
  useEffect(() => { stockMarketRef.current = stockMarket; }, [stockMarket]);

  const loadStatuses = useCallback(async () => {
    const [listStatus, klineStatus] = await Promise.allSettled([
      stocksApi.syncListStatus(),
      stocksApi.syncKlineStatus(),
    ]);
    const nextList = listStatus.status === 'fulfilled' ? listStatus.value : null;
    const nextKline = klineStatus.status === 'fulfilled' ? klineStatus.value : null;
    if (nextList) setListSyncStatus(nextList);
    if (nextKline) setKlineSyncStatus(nextKline);
    return { listStatus: nextList, klineStatus: nextKline };
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(() => {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      const { listStatus, klineStatus } = await loadStatuses();
      if (!isActiveStatus(listStatus) && !isActiveStatus(klineStatus)) {
        stopPolling();
        if (listStatus?.status === 'success') {
          void loadStockList(1, stockSearchRef.current, stockMarketRef.current, false);
          void loadWatchlist();
        }
      }
    }, POLL_INTERVAL_MS);
  }, [loadStatuses, loadStockList, loadWatchlist, stopPolling]);

  useEffect(() => {
    void loadStatuses().then(({ listStatus, klineStatus }) => {
      if (isActiveStatus(listStatus) || isActiveStatus(klineStatus)) startPolling();
    });
    return stopPolling;
  }, [loadStatuses, startPolling, stopPolling]);

  const handleSyncList = useCallback(async () => {
    if (isStartingList || isActiveStatus(listSyncStatus)) return;
    stopPolling();
    setIsStartingList(true);
    setSyncError(null);
    try {
      const result = await stocksApi.syncList();
      if (result.success) {
        await loadStatuses();
        startPolling();
      }
    } catch (err: unknown) {
      setSyncError(err instanceof Error ? err.message : '同步列表启动失败');
    } finally {
      setIsStartingList(false);
    }
  }, [isStartingList, listSyncStatus, loadStatuses, startPolling, stopPolling]);

  const handleSyncKline = useCallback(async () => {
    if (isStartingKline || isActiveStatus(klineSyncStatus)) return;
    stopPolling();
    setIsStartingKline(true);
    setSyncError(null);
    try {
      const result = await stocksApi.syncKline();
      if (result.success) {
        await loadStatuses();
        startPolling();
      }
    } catch (err: unknown) {
      setSyncError(err instanceof Error ? err.message : '同步K线启动失败');
    } finally {
      setIsStartingKline(false);
    }
  }, [isStartingKline, klineSyncStatus, loadStatuses, startPolling, stopPolling]);

  return {
    listSyncStatus,
    klineSyncStatus,
    syncError,
    pollRef,
    handleSyncList,
    handleSyncKline,
    isListSyncingActive: isStartingList || isActiveStatus(listSyncStatus),
    isKlineSyncingActive: isStartingKline || isActiveStatus(klineSyncStatus),
  };
}
