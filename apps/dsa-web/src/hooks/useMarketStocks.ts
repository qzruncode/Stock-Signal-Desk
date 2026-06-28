import { useCallback, useEffect, useMemo, useState } from 'react';
import { watchlistApi, type WatchlistResponse } from '../api/watchlist';
import { stocksApi, type StockMetaItem } from '../api/stocks';
import { useTransientMessage } from './useTransientMessage';
import { useStockSyncPolling } from './useStockSyncPolling';
import { useStockVisibilityRefresh } from './useStockVisibilityRefresh';
import { useInfiniteScroll } from './useInfiniteScroll';

const PAGE_SIZE = 50;

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

  // Watchlist data for "已添加" badge
  const [watchlistData, setWatchlistData] = useState<WatchlistResponse | null>(null);

  // Alerts
  const [error, setError] = useState<string | null>(null);
  const { message: successMsg, showMessage: showSuccessMessage } = useTransientMessage();

  const loadStockList = useCallback(async (page: number, search: string, market: string, append: boolean) => {
    if (append) {
      setLoadingMore(true);
    } else {
      setStockLoading(true);
    }
    try {
      const result = await stocksApi.list({ page, page_size: PAGE_SIZE, search: search || undefined, market: market || undefined });
      if (append) {
        setAllStocks((prev) => [...prev, ...result.items]);
      } else {
        setAllStocks(result.items);
      }
      setStockTotal(result.total);
      setStockPage(result.page);
      setHasMore(result.page < result.total_pages);
    } catch {
      setError('加载股票列表失败');
    }
    finally {
      setStockLoading(false);
      setLoadingMore(false);
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

  const { syncStatus, syncError, handleSync, isSyncingActive } = useStockSyncPolling({
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
    void loadStockList(1, value, stockMarket, false);
  }, [stockMarket, loadStockList]);

  const handleMarketFilter = useCallback((value: string) => {
    setStockMarket(value);
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
    syncStatus,
    syncError,
    isSyncingActive,
    sentinelRef,
    watchlistCodes,
    handleStockSearch,
    handleMarketFilter,
    handleAddStock,
    handleSync,
  };
}