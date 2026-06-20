import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, ArrowLeft, Plus, Search, Shield, TrendingUp, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { watchlistApi, type WatchlistResponse } from '../api/watchlist';
import { stocksApi, type StockMetaItem, type SyncStatusResponse, type KlineStatusResponse } from '../api/stocks';
import { klineApi, type KlineResponse } from '../api/kline';
import { EmptyState, InlineAlert } from '../components/common';
import KLineChartPanel from '../components/KLineChartPanel';
import { cn } from '../utils/cn';
import { MARKET_LABELS, MARKET_COLORS } from '../utils/market';
import { useTransientMessage } from '../hooks/useTransientMessage';

const PAGE_SIZE = 50;

const MarketStocksPage: React.FC = () => {
  const navigate = useNavigate();

  // Sync status
  const [syncStatus, setSyncStatus] = useState<SyncStatusResponse | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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

  // Sentinel for infinite scroll
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  const loadSyncStatus = useCallback(async () => {
    try {
      const status = await stocksApi.syncStatus();
      setSyncStatus(status);
      return status;
    } catch { return null; }
  }, []);

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
    } catch { /* ignore */ }
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
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    document.title = '全市场股票 - Stock-Signal-Desk';
    void loadSyncStatus();
    void loadStockList(1, '', '', false);
    void loadWatchlist();

    // Re-sync watchlist when returning from portfolio page
    const onVisibility = () => {
      if (document.visibilityState === 'visible') void loadWatchlist();
    };
    document.addEventListener('visibilitychange', onVisibility);
    // Also listen for focus in case of same-tab navigation
    const onFocus = () => void loadWatchlist();
    window.addEventListener('focus', onFocus);

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('focus', onFocus);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Infinite scroll observer
  useEffect(() => {
    if (!sentinelRef.current) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasMore && !loadingMore && !stockLoading) {
          void loadStockList(stockPage + 1, stockSearch, stockMarket, true);
        }
      },
      { threshold: 0.1 },
    );
    observer.observe(sentinelRef.current);
    return () => observer.disconnect();
  }, [hasMore, loadingMore, stockLoading, stockPage, stockSearch, stockMarket, loadStockList]);

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
          pollRef.current = setInterval(async () => {
            try {
              const s = await stocksApi.syncStatus();
              setSyncStatus(s);
              if (s.status === 'success' || s.status === 'failed') {
                if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
                if (s.status === 'success') {
                  void loadStockList(1, stockSearch, stockMarket, false);
                }
              }
            } catch { /* ignore */ }
          }, 2000);
        }
      }
    } catch (err: unknown) {
      setSyncError(err instanceof Error ? err.message : '同步启动失败');
    } finally {
      setIsSyncing(false);
    }
  }, [isSyncing, loadSyncStatus, stockSearch, stockMarket, loadStockList]);

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

  // K-line modal
  const [klineModalStock, setKlineModalStock] = useState<{ code: string; name: string } | null>(null);
  const [klineModalData, setKlineModalData] = useState<KlineResponse | null>(null);
  const [klineModalLoading, setKlineModalLoading] = useState(false);
  const [klineModalError, setKlineModalError] = useState<string | null>(null);

  const openKlineModal = useCallback(async (stock: { code: string; name: string }) => {
    setKlineModalStock(stock);
    setKlineModalData(null);
    setKlineModalError(null);
    setKlineModalLoading(true);
    try {
      const result = await klineApi.getKline(stock.code, 250);
      setKlineModalData(result);
    } catch {
      setKlineModalData(null);
      setKlineModalError('获取 K 线数据失败');
    } finally {
      setKlineModalLoading(false);
    }
  }, []);

  const closeKlineModal = useCallback(() => {
    setKlineModalStock(null);
    setKlineModalData(null);
    setKlineModalLoading(false);
    setKlineModalError(null);
  }, []);

  // Verification modal
  const [verifyModalOpen, setVerifyModalOpen] = useState(false);
  const [verifyData, setVerifyData] = useState<KlineStatusResponse | null>(null);
  const [verifyLoading, setVerifyLoading] = useState(false);

  const openVerifyModal = useCallback(async () => {
    setVerifyModalOpen(true);
    setVerifyLoading(true);
    try {
      const result = await stocksApi.getKlineStatus();
      setVerifyData(result);
    } catch {
      setVerifyData(null);
    } finally {
      setVerifyLoading(false);
    }
  }, []);

  const closeVerifyModal = useCallback(() => {
    setVerifyModalOpen(false);
    setVerifyData(null);
  }, []);

  const isSyncingActive = isSyncing || syncStatus?.status === 'running' || syncStatus?.status === 'syncing_kline';

  return (
    <div className="mx-auto flex h-[calc(100vh-2rem)] w-full max-w-[960px] flex-col gap-4 overflow-hidden px-3 py-4 sm:px-5">
      {/* Header — fixed at top */}
      <div className="flex shrink-0 items-center gap-4">
        <button
          type="button"
          onClick={() => navigate('/')}
          className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-600 transition hover:border-cyan-300 hover:text-cyan-700"
          aria-label="返回首页"
        >
          <ArrowLeft className="h-5 w-5" />
        </button>
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-indigo-700">Market Browser</p>
          <h1 className="text-2xl font-semibold text-slate-950">A 股全市场股票</h1>
          <p className="mt-1 text-sm text-slate-500">浏览全部 A 股市场股票，搜索、筛选并添加至自选股</p>
        </div>
      </div>

      {/* Alerts — fixed at top */}
      <div className="shrink-0 space-y-2">
        {error ? (
          <InlineAlert variant="danger" title="操作失败" message={error} className="rounded-xl px-3 py-2 text-xs shadow-none" />
        ) : null}
        {successMsg ? (
          <InlineAlert variant="success" title="操作成功" message={successMsg} className="rounded-xl px-3 py-2 text-xs shadow-none" />
        ) : null}
        {syncError ? (
          <InlineAlert variant="danger" title="同步失败" message={syncError} className="rounded-xl px-3 py-2 text-xs shadow-none" />
        ) : null}
      </div>

      {/* Sync bar — fixed at top */}
      <div className="shrink-0 flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white/88 px-5 py-3 shadow-sm">
        <div className="flex items-center gap-3">
          <TrendingUp className="h-5 w-5 text-indigo-600" />
          <div>
            <p className="text-sm font-semibold text-slate-800">
              数据同步状态
              {syncStatus?.total ? (
                <span className="ml-2 inline-flex items-center rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-700">
                  {syncStatus.total} 只
                </span>
              ) : null}
            </p>
            <p className="text-xs text-slate-400">
              {syncStatus?.status === 'success'
                ? `最近同步: ${syncStatus.finished_at ? new Date(syncStatus.finished_at).toLocaleString() : '-'}`
                : syncStatus?.status === 'running'
                  ? `同步股票列表中... ${syncStatus.progress}/${syncStatus.total || '...'}`
                  : syncStatus?.status === 'syncing_kline'
                    ? `同步 K 线历史... ${syncStatus.kline_progress}/${syncStatus.kline_total || '...'}`
                    : syncStatus?.status === 'failed'
                      ? `同步失败: ${syncStatus.error || syncStatus.message}`
                      : syncStatus?.status === 'idle' && syncStatus.total > 0
                        ? `上次同步: ${syncStatus.finished_at ? new Date(syncStatus.finished_at).toLocaleString() : '-'}`
                        : '尚未同步'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={openVerifyModal}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-semibold text-slate-600 transition hover:border-indigo-300 hover:text-indigo-700"
            title="验证 K 线数据完整性"
          >
            <Shield className="h-4 w-4" />
            验证数据源
          </button>
          <button
            type="button"
            disabled={isSyncingActive}
            onClick={handleSync}
            className="inline-flex items-center gap-1.5 rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-indigo-700 disabled:opacity-50"
          >
            {isSyncingActive ? (
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
            ) : (
              <TrendingUp className="h-4 w-4" />
            )}
            立即同步
          </button>
        </div>
      </div>

      {/* Search and filter bar — fixed at top */}
      <div className="shrink-0 flex flex-wrap items-center gap-2 rounded-2xl border border-slate-200 bg-white/88 px-5 py-3 shadow-sm">
        <div className="relative flex-1 min-w-[180px]">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            value={stockSearch}
            onChange={(e) => handleStockSearch(e.target.value)}
            placeholder="搜索股票代码或名称..."
            className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-8 text-sm text-slate-800 placeholder:text-slate-400 focus:border-indigo-400 focus:outline-none focus:ring-4 focus:ring-indigo-100"
          />
          {stockSearch && (
            <button
              type="button"
              onClick={() => handleStockSearch('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
        <select
          value={stockMarket}
          onChange={(e) => handleMarketFilter(e.target.value)}
          className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-indigo-400 focus:outline-none"
        >
          <option value="">全部市场</option>
          <option value="sh">沪市主板</option>
          <option value="sz">深市主板</option>
          <option value="cyb">创业板</option>
          <option value="kcb">科创板</option>
          <option value="bj">北交所</option>
        </select>
      </div>

      {/* Stock list — fills remaining space, only scrollable area */}
      <div className="min-h-0 flex-1 overflow-y-auto rounded-2xl border border-slate-200 bg-white/88 shadow-sm">
        <div className="px-5 py-4">
          {stockLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo/20 border-t-indigo" />
            </div>
          ) : allStocks.length === 0 && (!syncStatus || syncStatus.total === 0) ? (
            <EmptyState
              title="尚未同步股票数据"
              description="点击上方「立即同步」按钮，从东方财富同步全部 A 股数据"
              className="border-dashed py-12"
            />
          ) : allStocks.length === 0 ? (
            <EmptyState
              title="无匹配结果"
              description="尝试调整搜索或市场筛选条件"
              className="border-dashed py-12"
            />
          ) : (
            <>
              <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
                {allStocks.map((stock) => {
                  const isInWatchlist = watchlistCodes.has(stock.code);
                  return (
                    <div
                      key={stock.code}
                      className={cn(
                        'flex items-center justify-between rounded-lg border px-3 py-2 text-xs transition',
                        isInWatchlist
                          ? 'border-emerald-200 bg-emerald-50/50'
                          : 'border-slate-100 bg-white hover:border-indigo-200 hover:bg-indigo-50/30',
                      )}
                    >
                      <div
                        className="min-w-0 flex-1 cursor-pointer"
                        onClick={() => navigate(`/analysis?symbol=${stock.code}`)}
                        title={`查看 ${stock.code} 分析`}
                      >
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono font-medium text-slate-700">{stock.code}</span>
                          <span className={cn(
                            'inline-flex rounded px-1.5 py-0.5 text-[10px] font-medium',
                            MARKET_COLORS[stock.market] || '',
                          )}>
                            {MARKET_LABELS[stock.market] || stock.market}
                          </span>
                        </div>
                        <div className="mt-0.5 truncate text-slate-500">{stock.name}</div>
                        {stock.pe_ttm != null && (
                          <div className="mt-0.5 text-[10px] text-slate-400">
                            PE: {stock.pe_ttm.toFixed(1)} | 市值: {stock.total_market_cap != null ? (stock.total_market_cap / 1e8).toFixed(1) + '亿' : '-'}
                          </div>
                        )}
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <button
                          type="button"
                          onClick={() => openKlineModal({ code: stock.code, name: stock.name })}
                          className="rounded-lg p-1.5 text-slate-400 transition hover:bg-indigo-100 hover:text-indigo-700"
                          title={`查看 ${stock.code} K线`}
                        >
                          <Activity className="h-3.5 w-3.5" />
                        </button>
                        {isInWatchlist ? (
                          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
                            已添加
                          </span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => handleAddStock(stock.code)}
                            className="rounded-lg p-1.5 text-slate-400 transition hover:bg-indigo-100 hover:text-indigo-700"
                            title={`添加 ${stock.code}`}
                          >
                            <Plus className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Infinite scroll sentinel */}
              <div ref={sentinelRef} className="flex items-center justify-center py-4">
                {loadingMore && (
                  <div className="flex items-center gap-2 text-xs text-slate-400">
                    <div className="h-4 w-4 animate-spin rounded-full border-2 border-indigo/20 border-t-indigo" />
                    加载更多...
                  </div>
                )}
                {!hasMore && allStocks.length > 0 && (
                  <p className="text-xs text-slate-400">
                    已加载全部 {stockTotal} 只股票
                  </p>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* K-line modal overlay */}
      {klineModalStock && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4 py-8"
          onClick={closeKlineModal}
        >
          <div
            className="flex w-full max-w-3xl flex-col rounded-2xl bg-white shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal header */}
            <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4">
              <div>
                <h2 className="text-lg font-semibold text-slate-900">
                  {klineModalStock.name}
                  <span className="ml-2 font-mono text-sm font-normal text-slate-500">{klineModalStock.code}</span>
                </h2>
                <p className="text-xs text-slate-400">日线 · 前复权 · 近 250 个交易日</p>
              </div>
              <button
                type="button"
                onClick={closeKlineModal}
                className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                aria-label="关闭"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            {/* Modal body */}
            <div className="min-h-0 flex-1 overflow-hidden px-4 py-4">
              <KLineChartPanel
                data={klineModalData}
                loading={klineModalLoading}
                error={klineModalError}
              />
            </div>
          </div>
        </div>
      )}

      {/* Verification modal overlay */}
      {verifyModalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4"
          onClick={closeVerifyModal}
        >
          <div
            className="w-full max-w-sm rounded-2xl bg-white p-6 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-900">数据源验证结果</h2>
              <button
                type="button"
                onClick={closeVerifyModal}
                className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            {verifyLoading ? (
              <div className="flex items-center justify-center py-8">
                <div className="h-6 w-6 animate-spin rounded-full border-2 border-indigo/20 border-t-indigo" />
              </div>
            ) : verifyData ? (
              <div className="space-y-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-slate-500">股票总数</span>
                  <span className="font-semibold text-slate-900">{verifyData.total_stocks} 只</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">K 线数据</span>
                  <span className="font-semibold text-emerald-600">
                    {verifyData.stocks_with_kline} 只
                    {verifyData.total_stocks > 0
                      ? `（${((verifyData.stocks_with_kline / verifyData.total_stocks) * 100).toFixed(1)}%）`
                      : ''}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">缺失数据</span>
                  <span className={cn('font-semibold', verifyData.missing > 0 ? 'text-red-500' : 'text-emerald-600')}>
                    {verifyData.missing} 只
                  </span>
                </div>
                {verifyData.latest_trading_day && (
                  <div className="flex justify-between border-t border-slate-100 pt-3">
                    <span className="text-slate-500">最近交易日</span>
                    <span className="font-medium text-slate-700">{verifyData.latest_trading_day}</span>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-center text-sm text-red-500 py-4">获取验证数据失败</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default MarketStocksPage;
