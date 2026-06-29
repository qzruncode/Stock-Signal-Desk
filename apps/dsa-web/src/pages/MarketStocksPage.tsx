import React from 'react';
import { ArrowLeft, Shield, TrendingUp } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { EmptyState, InlineAlert } from '../components/common';
import { StockCard, StockSearchBar, KlineModal, VerifyModal } from '../components/market';
import { useMarketStocks } from '../hooks/useMarketStocks';
import { useKlineModal } from '../hooks/useKlineModal';
import { useVerifyModal } from '../hooks/useVerifyModal';

const MarketStocksPage: React.FC = () => {
  const navigate = useNavigate();
  const {
    allStocks, stockTotal, stockSearch, stockMarket, stockLoading, loadingMore, hasMore,
    error, successMsg, syncStatus, syncError, isSyncingActive, sentinelRef, watchlistCodes,
    handleStockSearch, handleMarketFilter, handleAddStock, handleSync,
  } = useMarketStocks();

  const klineModal = useKlineModal();
  const verifyModal = useVerifyModal();

  return (
    <div className="mx-auto flex h-[calc(100vh-2rem)] w-full max-w-[960px] flex-col gap-4 overflow-hidden px-3 py-4 sm:px-5">
      {/* Header */}
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
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Market Browser</p>
          <h1 className="text-2xl font-semibold text-slate-950">A 股全市场股票</h1>
          <p className="mt-1 text-sm text-slate-500">浏览全部 A 股市场股票，搜索、筛选并添加至自选股</p>
        </div>
      </div>

      {/* Alerts */}
      {(error || successMsg || syncError) && (
        <div className="shrink-0 space-y-2">
          {error && (
            <InlineAlert variant="danger" title="操作失败" message={error} className="rounded-xl px-3 py-2 text-xs shadow-none" />
          )}
          {successMsg && (
            <InlineAlert variant="success" title="操作成功" message={successMsg} className="rounded-xl px-3 py-2 text-xs shadow-none" />
          )}
          {syncError && (
            <InlineAlert variant="danger" title="同步失败" message={syncError} className="rounded-xl px-3 py-2 text-xs shadow-none" />
          )}
        </div>
      )}

      {/* Sync bar */}
      <div className="flex shrink-0 items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white/88 px-5 py-3 shadow-sm">
        <div className="flex items-center gap-3">
          <TrendingUp className="h-5 w-5 text-cyan-600" />
          <div>
            <p className="text-sm font-semibold text-slate-800">
              数据同步状态
              {syncStatus?.total ? (
                <span className="ml-2 inline-flex items-center rounded-full bg-cyan-100 px-2 py-0.5 text-xs font-medium text-cyan-700">
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
            onClick={() => void verifyModal.show()}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-semibold text-slate-600 transition hover:border-cyan-300 hover:text-cyan-700"
            title="验证 K 线数据完整性"
          >
            <Shield className="h-4 w-4" />
            验证数据源
          </button>
          <button
            type="button"
            disabled={isSyncingActive}
            onClick={() => void handleSync()}
            className="inline-flex items-center gap-1.5 rounded-xl bg-cyan-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-cyan-700 disabled:opacity-50"
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

      {/* Search and filter bar */}
      <StockSearchBar
        search={stockSearch}
        market={stockMarket}
        onSearchChange={handleStockSearch}
        onMarketChange={handleMarketFilter}
      />

      {/* Stock list */}
      <div className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-slate-200 bg-white/88 shadow-sm">
        <div className="px-5 py-4">
          {stockLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
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
                {allStocks.map((stock) => (
                  <StockCard
                    key={stock.code}
                    stock={stock}
                    isInWatchlist={watchlistCodes.has(stock.code)}
                    onViewKline={klineModal.open}
                    onAddStock={handleAddStock}
                    onNavigate={navigate}
                  />
                ))}
              </div>

              {/* Infinite scroll sentinel */}
              <div ref={sentinelRef} className="flex items-center justify-center py-4">
                {loadingMore && (
                  <div className="flex items-center gap-2 text-xs text-slate-400">
                    <div className="h-4 w-4 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
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

      {/* K-line modal */}
      {klineModal.stock && (
        <KlineModal
          stock={klineModal.stock}
          data={klineModal.data}
          loading={klineModal.loading}
          error={klineModal.error}
          onClose={klineModal.close}
        />
      )}

      {/* Verification modal */}
      <VerifyModal
        open={verifyModal.open}
        data={verifyModal.data}
        loading={verifyModal.loading}
        onClose={verifyModal.hide}
      />
    </div>
  );
};

export default MarketStocksPage;