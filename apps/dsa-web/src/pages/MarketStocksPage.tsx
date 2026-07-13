import React, { useCallback, useState } from 'react';
import { ArrowLeft, RefreshCw, Shield } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { EmptyState, InlineAlert } from '../components/common';
import { StockCard, StockDetailDrawer, StockSearchBar, KlineModal, VerifyModal } from '../components/market';
import { useMarketStocks } from '../hooks/useMarketStocks';
import { useKlineModal } from '../hooks/useKlineModal';
import { useVerifyModal } from '../hooks/useVerifyModal';
import { cn } from '../utils/cn';
import type { StockMetaItem } from '../api/stocks';

const getListSyncLabel = (syncStatus: ReturnType<typeof useMarketStocks>['listSyncStatus']) => {
  if (!syncStatus) return '尚未同步';
  switch (syncStatus.status) {
    case 'success':
      return syncStatus.finished_at ? `列表已同步 · ${new Date(syncStatus.finished_at).toLocaleString()}` : '列表已同步';
    case 'running':
      if (syncStatus.total === 0) {
        return syncStatus.message || '股票列表拉取中';
      }
      return `同步列表中 ${syncStatus.progress}/${syncStatus.total || '...'}`;
    case 'failed':
      return syncStatus.error || syncStatus.message || '列表同步失败';
    case 'idle':
      return syncStatus.total > 0
        ? (syncStatus.finished_at ? `上次同步 · ${new Date(syncStatus.finished_at).toLocaleString()}` : '已就绪')
        : '尚未同步';
    default:
      return '尚未同步';
  }
};

const getKlineSyncLabel = (syncStatus: ReturnType<typeof useMarketStocks>['klineSyncStatus']) => {
  if (!syncStatus) return 'K线未同步';
  if (syncStatus.status === 'syncing_kline' || syncStatus.status === 'running') {
    return `同步 K 线 ${syncStatus.kline_progress}/${syncStatus.kline_total || '...'}`;
  }
  if (syncStatus.status === 'success') {
    return syncStatus.finished_at ? `K线已同步 · ${new Date(syncStatus.finished_at).toLocaleString()}` : 'K线已同步';
  }
  if (syncStatus.status === 'failed') return 'K线同步失败';
  return 'K线未同步';
};

const getFinancialSyncLabel = (syncStatus: ReturnType<typeof useMarketStocks>['financialSyncStatus']) => {
  if (!syncStatus) return '财报未同步';
  if (syncStatus.status === 'running' || syncStatus.status === 'syncing_kline') {
    return `同步财报中 ${syncStatus.progress}/${syncStatus.total || '...'}`;
  }
  if (syncStatus.status === 'success') {
    return syncStatus.finished_at ? `财报已同步 · ${new Date(syncStatus.finished_at).toLocaleString()}` : '财报已同步';
  }
  if (syncStatus.status === 'failed') return '财报同步失败';
  return '财报未同步';
};

const MarketStocksPage: React.FC = () => {
  const navigate = useNavigate();
  const {
    allStocks, stockTotal, stockSearch, stockMarket, stockLoading, loadingMore, hasMore,
    error, successMsg, syncError, isListSyncingActive, isKlineSyncingActive, isFinancialSyncingActive, sentinelRef, watchlistCodes,
    handleStockSearch, handleMarketFilter, handleAddStock, handleSyncList, handleSyncKline, handleSyncFinancial,
    listSyncStatus, klineSyncStatus, financialSyncStatus,
  } = useMarketStocks();

  const klineModal = useKlineModal();
  const verifyModal = useVerifyModal();
  const [detailStock, setDetailStock] = useState<StockMetaItem | null>(null);

  const hasAlert = Boolean(error || successMsg || syncError);
  const canSyncKline = listSyncStatus?.status === 'success' && (listSyncStatus.total > 0 || stockTotal > 0);
  const canSyncFinancial = canSyncKline;
  const shouldShowKlineBadge = Boolean(klineSyncStatus && klineSyncStatus.status !== 'idle');
  const shouldShowFinancialBadge = Boolean(financialSyncStatus && financialSyncStatus.status !== 'idle');
  const openDetail = useCallback((stock: StockMetaItem) => {
    setDetailStock(stock);
  }, []);
  const closeDetail = useCallback(() => {
    setDetailStock(null);
  }, []);
  const openKlineFromDetail = useCallback((stock: { code: string; name: string }) => {
    setDetailStock(null);
    void klineModal.open(stock);
  }, [klineModal]);

  return (
    <div className="flex h-dvh w-full flex-col gap-0 overflow-hidden px-4 pt-3 pb-2 sm:px-5 sm:pt-4 sm:pb-2">
      {/* Compact top bar: title + sync status + actions */}
      <header className="flex shrink-0 items-center justify-between gap-3 pb-2">
        <div className="flex min-w-0 items-center gap-2">
          <button
            type="button"
            onClick={() => navigate('/')}
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border bg-card text-muted-foreground transition hover:border-primary/40 hover:text-primary"
            aria-label="返回首页"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div className="flex min-w-0 items-baseline gap-2">
            <h1 className="truncate text-base font-semibold text-foreground">A 股全市场股票</h1>
            <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">
              {stockTotal > 0 ? `${stockTotal} 只` : ''}
            </span>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <span
            className={cn(
              'hidden items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium md:inline-flex',
              isListSyncingActive
                ? 'bg-primary/10 text-primary'
                : listSyncStatus?.status === 'success'
                  ? 'bg-success/10 text-success'
                  : listSyncStatus?.status === 'failed'
                    ? 'bg-[hsl(var(--color-danger-alert-bg)/0.1)] text-[hsl(var(--color-danger-alert-text))]'
                    : 'bg-muted text-muted-foreground',
            )}
            title={listSyncStatus?.status === 'failed' ? listSyncStatus.error || listSyncStatus.message || '列表同步失败' : undefined}
          >
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                isListSyncingActive ? 'animate-pulse bg-primary' : listSyncStatus?.status === 'success' ? 'bg-success' : listSyncStatus?.status === 'failed' ? 'bg-[hsl(var(--color-danger-alert-text))]' : 'bg-muted-foreground/60',
              )}
            />
            <span className="max-w-[220px] truncate">{getListSyncLabel(listSyncStatus)}</span>
            {listSyncStatus?.total ? (
              <span className="rounded-full bg-card px-1.5 py-0.5 text-[10px] text-muted-foreground">{listSyncStatus.total}</span>
            ) : null}
          </span>
          {shouldShowKlineBadge && klineSyncStatus && (
            <span
              className={cn(
                'hidden items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium lg:inline-flex',
                isKlineSyncingActive
                  ? 'bg-primary/10 text-primary'
                  : klineSyncStatus.status === 'success'
                    ? 'bg-success/10 text-success'
                    : klineSyncStatus.status === 'failed'
                      ? 'bg-[hsl(var(--color-danger-alert-bg)/0.1)] text-[hsl(var(--color-danger-alert-text))]'
                      : 'bg-muted text-muted-foreground',
              )}
              title={klineSyncStatus.status === 'failed' ? klineSyncStatus.error || klineSyncStatus.message || 'K线同步失败' : undefined}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  isKlineSyncingActive ? 'animate-pulse bg-primary' : klineSyncStatus.status === 'success' ? 'bg-success' : klineSyncStatus.status === 'failed' ? 'bg-[hsl(var(--color-danger-alert-text))]' : 'bg-muted-foreground/60',
                )}
              />
              <span className="max-w-[180px] truncate">{getKlineSyncLabel(klineSyncStatus)}</span>
            </span>
          )}
          {shouldShowFinancialBadge && financialSyncStatus && (
            <span
              className={cn(
                'hidden items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium xl:inline-flex',
                isFinancialSyncingActive
                  ? 'bg-primary/10 text-primary'
                  : financialSyncStatus.status === 'success'
                    ? 'bg-success/10 text-success'
                    : financialSyncStatus.status === 'failed'
                      ? 'bg-[hsl(var(--color-danger-alert-bg)/0.1)] text-[hsl(var(--color-danger-alert-text))]'
                      : 'bg-muted text-muted-foreground',
              )}
              title={financialSyncStatus.status === 'failed' ? financialSyncStatus.error || financialSyncStatus.message || '财报同步失败' : undefined}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  isFinancialSyncingActive ? 'animate-pulse bg-primary' : financialSyncStatus.status === 'success' ? 'bg-success' : financialSyncStatus.status === 'failed' ? 'bg-[hsl(var(--color-danger-alert-text))]' : 'bg-muted-foreground/60',
                )}
              />
              <span className="max-w-[180px] truncate">{getFinancialSyncLabel(financialSyncStatus)}</span>
            </span>
          )}

          <button
            type="button"
            onClick={() => void verifyModal.show()}
            className="inline-flex h-[34px] items-center gap-1.5 rounded-lg border border-border bg-card px-3 text-xs font-semibold text-muted-foreground transition hover:border-primary/40 hover:text-primary"
            title="验证 K 线数据完整性"
          >
            <Shield className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">验证</span>
          </button>
          <button
            type="button"
            disabled={isListSyncingActive}
            onClick={() => void handleSyncList()}
            className="inline-flex h-[34px] items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-semibold text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50"
          >
            {isListSyncingActive ? (
              <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary-foreground/30 border-t-primary-foreground" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            同步列表
          </button>
          <button
            type="button"
            disabled={!canSyncFinancial || isFinancialSyncingActive}
            onClick={() => void handleSyncFinancial()}
            className="inline-flex h-[34px] items-center gap-1.5 rounded-lg border border-primary/30 bg-card px-3 text-xs font-semibold text-primary transition hover:bg-primary/10 disabled:cursor-not-allowed disabled:border-border disabled:text-muted-foreground disabled:opacity-60"
            title={!canSyncFinancial ? '请先同步股票列表' : '按报告期拉全市场业绩快报写入 stock_meta'}
          >
            {isFinancialSyncingActive ? (
              <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            同步财报
          </button>
          <button
            type="button"
            disabled={!canSyncKline || isKlineSyncingActive}
            onClick={() => void handleSyncKline()}
            className="inline-flex h-[34px] items-center gap-1.5 rounded-lg border border-primary/30 bg-card px-3 text-xs font-semibold text-primary transition hover:bg-primary/10 disabled:cursor-not-allowed disabled:border-border disabled:text-muted-foreground disabled:opacity-60"
            title={!canSyncKline ? '请先同步股票列表' : undefined}
          >
            {isKlineSyncingActive ? (
              <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            同步K线
          </button>
        </div>
      </header>

      {/* Search + Stock list (unified card) */}
      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border bg-card">
        <StockSearchBar
          search={stockSearch}
          market={stockMarket}
          onSearchChange={handleStockSearch}
          onMarketChange={handleMarketFilter}
        />

        <div className="h-px shrink-0 bg-border" />

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="px-3 py-3 sm:px-4">
          {stockLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="h-7 w-7 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
            </div>
          ) : allStocks.length === 0 && (!listSyncStatus || listSyncStatus.total === 0) ? (
            <EmptyState
              title="尚未同步股票数据"
              description="点击右上角「同步列表」按钮，从东方财富同步全部 A 股数据"
              className="border-dashed py-10"
            />
          ) : allStocks.length === 0 ? (
            <EmptyState
              title="无匹配结果"
              description="尝试调整搜索或市场筛选条件"
              className="border-dashed py-10"
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
                    onViewDetails={openDetail}
                    onAddStock={handleAddStock}
                  />
                ))}
              </div>

              <div ref={sentinelRef} className="flex items-center justify-center py-3">
                {loadingMore && (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
                    加载更多...
                  </div>
                )}
                {!hasMore && allStocks.length > 0 && (
                  <p className="text-xs text-muted-foreground">已加载全部 {stockTotal} 只股票</p>
                )}
              </div>
            </>
          )}
          </div>

          {/* Floating alerts overlay - doesn't take layout space */}
          {hasAlert && (
            <div className="pointer-events-none sticky bottom-3 z-10 mx-3 flex flex-col gap-1.5 sm:mx-4">
              {error && (
                <InlineAlert variant="danger" title="操作失败" message={error} className="pointer-events-auto rounded-md px-3 py-1.5 text-xs shadow-md" />
              )}
              {successMsg && (
                <InlineAlert variant="success" title="操作成功" message={successMsg} className="pointer-events-auto rounded-md px-3 py-1.5 text-xs shadow-md" />
              )}
              {syncError && (
                <InlineAlert variant="danger" title="同步失败" message={syncError} className="pointer-events-auto rounded-md px-3 py-1.5 text-xs shadow-md" />
              )}
            </div>
          )}
        </div>
      </div>

      {/* K-line modal */}
      <StockDetailDrawer
        key={detailStock?.code ?? 'closed'}
        open={Boolean(detailStock)}
        stock={detailStock}
        onClose={closeDetail}
        onViewKline={openKlineFromDetail}
      />

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
        syncStatus={verifyModal.syncStatus}
        onSyncMissing={verifyModal.syncMissing}
        onClose={verifyModal.hide}
      />
    </div>
  );
};

export default MarketStocksPage;
