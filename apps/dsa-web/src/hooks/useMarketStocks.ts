import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { watchlistApi, type WatchlistResponse } from '../api/watchlist';
import { stocksApi, type StockMetaItem } from '../api/stocks';
import { useTransientMessage } from './useTransientMessage';
import { useStockSyncPolling } from './useStockSyncPolling';
import { useStockVisibilityRefresh } from './useStockVisibilityRefresh';
import { useInfiniteScroll } from './useInfiniteScroll';

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

export function useMarketStocks() {
  // Stock list state
  const [allStocks, setAllStocks] = useState<StockMetaItem[]>([]);
  const [stockTotal, setStockTotal] = useState(0);
  const [stockPage, setStockPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [stockSearch, setStockSearch] = useState('');
  const [stockMarket, setStockMarket] = useState('');
  const [stockLoading, setStockLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const requestSeqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const inFlightAppendPagesRef = useRef<Set<number>>(new Set());
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeListKeyRef = useRef('');

  // Watchlist data for "已添加" badge
  const [watchlistData, setWatchlistData] = useState<WatchlistResponse | null>(null);

  // Alerts
  const [error, setError] = useState<string | null>(null);
  const { message: successMsg, showMessage: showSuccessMessage } = useTransientMessage();

  const loadStockList = useCallback(async (page: number, search: string, market: string, append: boolean) => {
    const listKey = `${search}\0${market}`;
    if (append) {
      if (inFlightAppendPagesRef.current.has(page)) return;
      inFlightAppendPagesRef.current.add(page);
    } else {
      abortRef.current?.abort();
      activeListKeyRef.current = listKey;
    }
    const requestSeq = ++requestSeqRef.current;
    const abortController = new AbortController();
    abortRef.current = abortController;

    if (append) {
      setLoadingMore(true);
    } else {
      setStockLoading(true);
    }
    try {
      const result = await stocksApi.list({
        page,
        page_size: PAGE_SIZE,
        search: search || undefined,
        market: market || undefined,
        count: !append,
        signal: abortController.signal,
      });
      if (requestSeq !== requestSeqRef.current && !append) return;
      if (append && activeListKeyRef.current !== listKey) return;
      if (append) {
        setAllStocks((prev) => [...prev, ...result.items]);
      } else {
        setAllStocks(result.items);
      }
      if (!append || result.total > 0) {
        setStockTotal(result.total);
      }
      setStockPage(result.page);
      setHasMore(result.has_more ?? result.page < result.total_pages);
    } catch (err: unknown) {
      if (
        (err instanceof DOMException && err.name === 'AbortError') ||
        (typeof err === 'object' && err !== null && 'code' in err && err.code === 'ERR_CANCELED')
      ) return;
      setError('加载股票列表失败');
    }
    finally {
      if (abortRef.current === abortController) {
        abortRef.current = null;
      }
      if (!append && requestSeq === requestSeqRef.current) {
        setStockLoading(false);
      }
      if (append) {
        inFlightAppendPagesRef.current.delete(page);
        setLoadingMore(false);
      }
    }
  }, []);

  // Load watchlist for "已添加" badge
  const loadWatchlist = useCallback(async () => {
    try {
      const result = await watchlistApi.get();
      setWatchlistData(result);
    } catch {
      console.error('加载自选股列表失败');
    }
  }, []);

  const {
    listSyncStatus,
    klineSyncStatus,
    syncError,
    handleSyncList,
    handleSyncKline,
    isListSyncingActive,
    isKlineSyncingActive,
  } = useStockSyncPolling({
    loadWatchlist,
    loadStockList,
    stockSearch,
    stockMarket,
  });

  useStockVisibilityRefresh(loadWatchlist);

  useEffect(() => {
    void loadStockList(1, '', '', false);
    void loadWatchlist();
  }, [loadStockList, loadWatchlist]);

  const { sentinelRef } = useInfiniteScroll({
    hasMore,
    loadingMore,
    loading: stockLoading,
    onLoadMore: () => {
      loadStockList(stockPage + 1, stockSearch, stockMarket, true);
    },
  });

  const handleStockSearch = useCallback((value: string) => {
    setStockSearch(value);
    if (searchDebounceRef.current) {
      clearTimeout(searchDebounceRef.current);
    }
    searchDebounceRef.current = setTimeout(() => {
      void loadStockList(1, value, stockMarket, false);
    }, SEARCH_DEBOUNCE_MS);
  }, [stockMarket, loadStockList]);

  const handleMarketFilter = useCallback((value: string) => {
    setStockMarket(value);
    if (searchDebounceRef.current) {
      clearTimeout(searchDebounceRef.current);
      searchDebounceRef.current = null;
    }
    void loadStockList(1, stockSearch, value, false);
  }, [stockSearch, loadStockList]);

  const handleAddStock = useCallback(async (code: string) => {
    setError(null);
    try {
      const result = await watchlistApi.add([code]);
      setWatchlistData({ codes: result.codes, count: result.count, configVersion: result.configVersion });
      showSuccessMessage(`已添加 ${code}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '添加失败');
    }
  }, [showSuccessMessage]);

  const watchlistCodes = useMemo(() => new Set(watchlistData?.codes || []), [watchlistData]);

  useEffect(() => () => {
    if (searchDebounceRef.current) {
      clearTimeout(searchDebounceRef.current);
    }
    abortRef.current?.abort();
  }, []);

  return {
    allStocks,
    stockTotal,
    stockSearch,
    stockMarket,
    stockLoading,
    loadingMore,
    hasMore,
    error,
    successMsg,
    listSyncStatus,
    klineSyncStatus,
    syncError,
    isListSyncingActive,
    isKlineSyncingActive,
    sentinelRef,
    watchlistCodes,
    handleStockSearch,
    handleMarketFilter,
    handleAddStock,
    handleSyncList,
    handleSyncKline,
  };
}
