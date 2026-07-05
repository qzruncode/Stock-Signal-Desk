import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { stocksApi, type SyncStatusResponse } from '../api/stocks';

export interface UseStockSyncPollingResult {
  listSyncStatus: SyncStatusResponse | null;
  klineSyncStatus: SyncStatusResponse | null;
  financialSyncStatus: SyncStatusResponse | null;
  syncError: string | null;
  pollRef: RefObject<ReturnType<typeof setTimeout> | null>;
  handleSyncList: () => Promise<void>;
  handleSyncKline: () => Promise<void>;
  handleSyncFinancial: () => Promise<void>;
  isListSyncingActive: boolean;
  isKlineSyncingActive: boolean;
  isFinancialSyncingActive: boolean;
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

type PollTargets = { list?: boolean; kline?: boolean; financial?: boolean };

const hasPollTarget = (targets: PollTargets) => Boolean(targets.list || targets.kline || targets.financial);

const mergePollTargets = (current: PollTargets, next: PollTargets): PollTargets => ({
  list: Boolean(current.list || next.list),
  kline: Boolean(current.kline || next.kline),
  financial: Boolean(current.financial || next.financial),
});

export function useStockSyncPolling({
  loadWatchlist,
  loadStockList,
  stockSearch,
  stockMarket,
}: UseStockSyncPollingOptions): UseStockSyncPollingResult {
  const [listSyncStatus, setListSyncStatus] = useState<SyncStatusResponse | null>(null);
  const [klineSyncStatus, setKlineSyncStatus] = useState<SyncStatusResponse | null>(null);
  const [financialSyncStatus, setFinancialSyncStatus] = useState<SyncStatusResponse | null>(null);
  const [isStartingList, setIsStartingList] = useState(false);
  const [isStartingKline, setIsStartingKline] = useState(false);
  const [isStartingFinancial, setIsStartingFinancial] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollStartedAtRef = useRef(0);
  const activePollTargetsRef = useRef<PollTargets>({});
  const needsListRefreshRef = useRef(false);

  const stockSearchRef = useRef(stockSearch);
  const stockMarketRef = useRef(stockMarket);

  useEffect(() => { stockSearchRef.current = stockSearch; }, [stockSearch]);
  useEffect(() => { stockMarketRef.current = stockMarket; }, [stockMarket]);

  const loadStatuses = useCallback(async (options: PollTargets = { list: true, kline: true, financial: true }) => {
    const [listStatus, klineStatus, financialStatus] = await Promise.allSettled([
      options.list ? stocksApi.syncListStatus() : Promise.resolve(null),
      options.kline ? stocksApi.syncKlineStatus() : Promise.resolve(null),
      options.financial ? stocksApi.syncFinancialStatus() : Promise.resolve(null),
    ]);
    const nextList = listStatus.status === 'fulfilled' ? listStatus.value : null;
    const nextKline = klineStatus.status === 'fulfilled' ? klineStatus.value : null;
    const nextFinancial = financialStatus.status === 'fulfilled' ? financialStatus.value : null;
    if (nextList) setListSyncStatus(nextList);
    if (nextKline) setKlineSyncStatus(nextKline);
    if (nextFinancial) setFinancialSyncStatus(nextFinancial);
    return { listStatus: nextList, klineStatus: nextKline, financialStatus: nextFinancial };
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
    activePollTargetsRef.current = {};
    needsListRefreshRef.current = false;
  }, []);

  const startPolling = useCallback((options: PollTargets = { list: true, kline: true, financial: true }) => {
    if (!hasPollTarget(options)) return;
    activePollTargetsRef.current = mergePollTargets(activePollTargetsRef.current, options);
    if (pollRef.current) return;
    pollStartedAtRef.current = Date.now();

    const tick = async () => {
      pollRef.current = null;
      const targets = activePollTargetsRef.current;
      const { listStatus, klineStatus, financialStatus } = await loadStatuses(targets);
      if (listStatus?.status === 'success' || financialStatus?.status === 'success') {
        needsListRefreshRef.current = true;
      }
      const nextTargets: PollTargets = {
        list: targets.list && isActiveStatus(listStatus),
        kline: targets.kline && isActiveStatus(klineStatus),
        financial: targets.financial && isActiveStatus(financialStatus),
      };
      activePollTargetsRef.current = nextTargets;
      const listStillActive = Boolean(nextTargets.list);
      const klineStillActive = Boolean(nextTargets.kline);
      const financialStillActive = Boolean(nextTargets.financial);
      if (!listStillActive && !klineStillActive && !financialStillActive) {
        // 列表或财报完成时都刷新列表（让首页重新拉新数据）
        const shouldRefreshList = needsListRefreshRef.current;
        stopPolling();
        if (shouldRefreshList) {
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
    void loadStatuses().then(({ listStatus, klineStatus, financialStatus }) => {
      startPolling({
        list: isActiveStatus(listStatus),
        kline: isActiveStatus(klineStatus),
        financial: isActiveStatus(financialStatus),
      });
    });
    return stopPolling;
  }, [loadStatuses, startPolling, stopPolling]);

  const handleSyncList = useCallback(async () => {
    if (isStartingList || isActiveStatus(listSyncStatus)) return;
    setIsStartingList(true);
    setSyncError(null);
    try {
      const result = await stocksApi.syncList();
      if (result.success) {
        await loadStatuses({ list: true, kline: false, financial: false });
        startPolling({ list: true, kline: false, financial: false });
      }
    } catch (err: unknown) {
      setSyncError(err instanceof Error ? err.message : '同步列表启动失败');
    } finally {
      setIsStartingList(false);
    }
  }, [isStartingList, listSyncStatus, loadStatuses, startPolling]);

  const handleSyncKline = useCallback(async () => {
    if (isStartingKline || isActiveStatus(klineSyncStatus)) return;
    setIsStartingKline(true);
    setSyncError(null);
    try {
      const result = await stocksApi.syncKline();
      if (result.success) {
        await loadStatuses({ list: false, kline: true, financial: false });
        startPolling({ list: false, kline: true, financial: false });
      }
    } catch (err: unknown) {
      setSyncError(err instanceof Error ? err.message : '同步K线启动失败');
    } finally {
      setIsStartingKline(false);
    }
  }, [isStartingKline, klineSyncStatus, loadStatuses, startPolling]);

  const handleSyncFinancial = useCallback(async () => {
    if (isStartingFinancial || isActiveStatus(financialSyncStatus)) return;
    setIsStartingFinancial(true);
    setSyncError(null);
    try {
      const result = await stocksApi.syncFinancial();
      if (result.success) {
        await loadStatuses({ list: false, kline: false, financial: true });
        startPolling({ list: false, kline: false, financial: true });
      }
    } catch (err: unknown) {
      setSyncError(err instanceof Error ? err.message : '同步财报启动失败');
    } finally {
      setIsStartingFinancial(false);
    }
  }, [isStartingFinancial, financialSyncStatus, loadStatuses, startPolling]);

  return {
    listSyncStatus,
    klineSyncStatus,
    financialSyncStatus,
    syncError,
    pollRef,
    handleSyncList,
    handleSyncKline,
    handleSyncFinancial,
    isListSyncingActive: isStartingList || isActiveStatus(listSyncStatus),
    isKlineSyncingActive: isStartingKline || isActiveStatus(klineSyncStatus),
    isFinancialSyncingActive: isStartingFinancial || isActiveStatus(financialSyncStatus),
  };
}
