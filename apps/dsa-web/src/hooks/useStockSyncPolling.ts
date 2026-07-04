import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { stocksApi, type SyncStatusResponse } from '../api/stocks';

export interface UseStockSyncPollingResult {
  listSyncStatus: SyncStatusResponse | null;
  klineSyncStatus: SyncStatusResponse | null;
  syncError: string | null;
  pollRef: RefObject<ReturnType<typeof setTimeout> | null>;
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

const FAST_POLL_INTERVAL_MS = 2000;
const SLOW_POLL_INTERVAL_MS = 5000;
const SLOW_POLL_AFTER_MS = 30000;

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
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollStartedAtRef = useRef(0);

  const stockSearchRef = useRef(stockSearch);
  const stockMarketRef = useRef(stockMarket);

  useEffect(() => { stockSearchRef.current = stockSearch; }, [stockSearch]);
  useEffect(() => { stockMarketRef.current = stockMarket; }, [stockMarket]);

  const loadStatuses = useCallback(async (options: { list?: boolean; kline?: boolean } = { list: true, kline: true }) => {
    const [listStatus, klineStatus] = await Promise.allSettled([
      options.list ? stocksApi.syncListStatus() : Promise.resolve(null),
      options.kline ? stocksApi.syncKlineStatus() : Promise.resolve(null),
    ]);
    const nextList = listStatus.status === 'fulfilled' ? listStatus.value : null;
    const nextKline = klineStatus.status === 'fulfilled' ? klineStatus.value : null;
    if (nextList) setListSyncStatus(nextList);
    if (nextKline) setKlineSyncStatus(nextKline);
    return { listStatus: nextList, klineStatus: nextKline };
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback((options: { list?: boolean; kline?: boolean } = { list: true, kline: true }) => {
    if (!options.list && !options.kline) return;
    if (pollRef.current) return;
    pollStartedAtRef.current = Date.now();

    const tick = async () => {
      pollRef.current = null;
      const { listStatus, klineStatus } = await loadStatuses(options);
      const listStillActive = options.list ? isActiveStatus(listStatus) : false;
      const klineStillActive = options.kline ? isActiveStatus(klineStatus) : false;
      if (!listStillActive && !klineStillActive) {
        stopPolling();
        if (listStatus?.status === 'success') {
          void loadStockList(1, stockSearchRef.current, stockMarketRef.current, false);
          void loadWatchlist();
        }
        return;
      }

      const elapsed = Date.now() - pollStartedAtRef.current;
      const nextDelay = elapsed > SLOW_POLL_AFTER_MS ? SLOW_POLL_INTERVAL_MS : FAST_POLL_INTERVAL_MS;
      pollRef.current = setTimeout(tick, nextDelay);
    };

    pollRef.current = setTimeout(tick, FAST_POLL_INTERVAL_MS);
  }, [loadStatuses, loadStockList, loadWatchlist, stopPolling]);

  useEffect(() => {
    void loadStatuses().then(({ listStatus, klineStatus }) => {
      startPolling({
        list: isActiveStatus(listStatus),
        kline: isActiveStatus(klineStatus),
      });
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
        await loadStatuses({ list: true, kline: false });
        startPolling({ list: true, kline: false });
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
        await loadStatuses({ list: false, kline: true });
        startPolling({ list: false, kline: true });
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
