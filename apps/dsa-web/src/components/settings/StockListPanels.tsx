import { Controller } from 'react-hook-form';
import type { UseFormReturn } from 'react-hook-form';
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  FolderPlus,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import { Badge, Button, EmptyState } from '../common';
import type { StockMetaItem, StocksListResponse } from '../../api/stocks';
import type { WatchlistGroup, WatchlistResponse } from '../../api/watchlist';
import { cn } from '../../utils/cn';
import { StockListCompactSelect as CompactSelect } from './StockListCompactSelect';

type StockListViewMode = 'market' | 'watchlist';
interface MarketFormValues {
  search: string;
  market: string;
}
export interface WatchlistFormValues {
  activeGroupId: string;
  newGroupName: string;
  renameValue: string;
  addQuery: string;
  batchInput: string;
}
type Notice = { type: 'success' | 'error'; message: string } | null;
type PageSetter = (value: number | ((current: number) => number)) => void;

interface StockListPanelsProps {
  view: StockListViewMode;
  setView: (value: StockListViewMode) => void;
  setMarketSearch: (value: string) => void;
  setMarketFilter: (value: string) => void;
  setMarketPage: PageSetter;
  setMarketExportConfirming: (value: boolean) => void;
  marketSearch: string;
  marketFilter: string;
  marketPage: number;
  marketForm: UseFormReturn<MarketFormValues>;
  marketValues: Partial<MarketFormValues>;
  submitMarketSearch: (values: MarketFormValues) => void;
  marketData: StocksListResponse | null;
  marketLoading: boolean;
  busyAction: string | null;
  marketExportConfirming: boolean;
  exportAllMarketMarkdown: () => void | Promise<void>;
  addToDefaultWatchlist: (code: string) => void | Promise<void>;
  watchlistCodes: Set<string>;
  watchlistForm: UseFormReturn<WatchlistFormValues>;
  watchlistValues: Partial<WatchlistFormValues>;
  watchlist: WatchlistResponse | null;
  groups: WatchlistGroup[];
  setNotice: (notice: Notice) => void;
  setEditingGroupId: (value: string | null) => void;
  setDeleteTarget: (value: WatchlistGroup | null) => void;
  setCreateGroupOpen: (value: boolean) => void;
  candidateLoading: boolean;
  candidateItems: StockMetaItem[];
  watchlistLoading: boolean;
  activeCodes: string[];
  activeGroup: WatchlistGroup | null;
  addToActiveGroup: (codes: string[]) => void | Promise<void>;
  submitBatchAdd: (values: WatchlistFormValues) => void | Promise<void>;
  stockNameByCode: Map<string, string>;
  removeFromActiveGroup: (code: string) => void | Promise<void>;
  exportConfirming: boolean;
  setExportConfirming: (value: boolean) => void;
  activeCodeCount: number;
  exportActiveGroupMarkdown: () => void;
}

const DEFAULT_GROUP_ID = 'default';
const DEFAULT_GROUP_NAME = '我的自选股';
const COMPACT_BUTTON_CLASS = 'h-7 gap-1 rounded-md px-2 text-[11px]';
const CONTROL_LABEL_CLASS = 'w-9 shrink-0 text-[11px] font-medium leading-4 text-secondary-text';
const MARKET_OPTIONS = [
  { value: '', label: '全部市场' },
  { value: 'sh', label: '沪市主板' },
  { value: 'sz', label: '深市主板' },
  { value: 'cyb', label: '创业板' },
  { value: 'kcb', label: '科创板' },
  { value: 'bj', label: '北交所' },
];

function marketLabel(market: string): string {
  return MARKET_OPTIONS.find((item) => item.value === market)?.label || market || '其他';
}

function marketClass(market: string): string {
  const styles: Record<string, string> = {
    sh: 'bg-red-100 text-red-700',
    sz: 'bg-blue-100 text-blue-700',
    cyb: 'bg-purple-100 text-purple-700',
    kcb: 'bg-amber-100 text-amber-700',
    bj: 'bg-emerald-100 text-emerald-700',
  };
  return styles[market] || 'bg-slate-100 text-slate-600';
}

function groupName(group: WatchlistGroup | null): string {
  const name = group?.name?.trim();
  return !name || name.toLowerCase() === 'null' || name.toLowerCase() === 'undefined'
    ? '未命名分组'
    : name;
}

export function StockListPanels({
  view,
  setView,
  setMarketSearch,
  setMarketFilter,
  setMarketPage,
  setMarketExportConfirming,
  marketSearch,
  marketFilter,
  marketPage,
  marketForm,
  marketValues,
  submitMarketSearch,
  marketData,
  marketLoading,
  busyAction,
  marketExportConfirming,
  exportAllMarketMarkdown,
  addToDefaultWatchlist,
  watchlistCodes,
  watchlistForm,
  watchlistValues,
  watchlist,
  groups,
  setNotice,
  setEditingGroupId,
  setDeleteTarget,
  setCreateGroupOpen,
  candidateLoading,
  candidateItems,
  watchlistLoading,
  activeCodes,
  activeGroup,
  addToActiveGroup,
  submitBatchAdd,
  stockNameByCode,
  removeFromActiveGroup,
  exportConfirming,
  setExportConfirming,
  activeCodeCount,
  exportActiveGroupMarkdown,
}: StockListPanelsProps) {
  return (
      <div className="terminal-card flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl">
        <div className="flex-none border-b border-border/60 px-3 py-2 sm:px-4">
          <div className="flex w-fit items-center gap-0.5 rounded-lg bg-elevated/70 p-0.5" role="tablist" aria-label="股票数据视图">
            <button
              type="button"
              role="tab"
              aria-selected={view === 'market'}
              onClick={() => {
                setMarketExportConfirming(false);
                setView('market');
              }}
              className={cn(
                'rounded-md px-2 py-1 text-[11px] transition',
                view === 'market' ? 'bg-white font-semibold text-foreground shadow-sm' : 'text-secondary-text hover:text-foreground',
              )}
            >
              全市场股票
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === 'watchlist'}
              onClick={() => {
                setMarketExportConfirming(false);
                setView('watchlist');
              }}
              className={cn(
                'rounded-md px-2 py-1 text-[11px] transition',
                view === 'watchlist' ? 'bg-white font-semibold text-foreground shadow-sm' : 'text-secondary-text hover:text-foreground',
              )}
            >
              我的自选股
            </button>
          </div>
        </div>

        {view === 'market' ? (
          <div className="flex min-h-0 flex-1 flex-col space-y-2.5 p-2.5 sm:p-3">
            <form onSubmit={marketForm.handleSubmit(submitMarketSearch)} className="flex shrink-0 flex-wrap items-center gap-1 rounded-xl border border-border/70 bg-white p-1 shadow-sm">
              <div className="relative min-w-[180px] flex-1">
                <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                <input
                  {...marketForm.register('search')}
                  placeholder="搜索代码或名称"
                  className="h-8 w-full rounded-md border-0 bg-transparent pl-7 pr-7 text-[11px] text-foreground placeholder:text-muted-foreground focus:bg-elevated/45 focus:outline-none focus:ring-0"
                />
                {marketValues.search ? (
                  <button
                    type="button"
                    aria-label="清除搜索"
                    onClick={() => { marketForm.setValue('search', ''); setMarketSearch(''); setMarketPage(1); setMarketExportConfirming(false); }}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-full p-0.5 text-muted-foreground transition hover:bg-elevated hover:text-foreground"
                  >
                    <X className="size-3" />
                  </button>
                ) : null}
              </div>
              <Controller
                name="market"
                control={marketForm.control}
                render={({ field }) => (
                  <CompactSelect
                    value={field.value}
                    options={MARKET_OPTIONS}
                    ariaLabel="市场筛选"
                    onChange={(value) => {
                      field.onChange(value);
                      setMarketFilter(value);
                      setMarketPage(1);
                      setMarketExportConfirming(false);
                    }}
                  />
                )}
              />
              <Button type="submit" variant="primary" size="sm" className={COMPACT_BUTTON_CLASS}>
                <Search className="size-3" />
                查询
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className={COMPACT_BUTTON_CLASS}
                disabled={!marketData?.total || marketLoading || busyAction === 'export-market'}
                onClick={() => setMarketExportConfirming(true)}
              >
                <Download className="size-3" />
                {marketSearch || marketFilter ? '导出当前筛选' : '导出全市场'}
              </Button>
            </form>

            {marketLoading ? (
              <div className="flex min-h-[22rem] items-center justify-center text-muted-foreground">
                <RefreshCw className="size-6 animate-spin text-cyan" />
              </div>
            ) : marketData?.items.length ? (
              <>
                <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border/60">
                  <div className="min-h-0 flex-1 overflow-y-auto divide-y divide-border/50">
                    {marketData.items.map((stock) => {
                      const inWatchlist = watchlistCodes.has(stock.code);
                      return (
                        <div key={stock.code} className="flex min-w-0 items-center gap-2 px-2.5 py-1.5 transition hover:bg-elevated/35 sm:gap-3 sm:px-3">
                          <div className="flex min-w-0 flex-[1.25] items-center gap-1.5">
                            <span className="shrink-0 font-mono text-xs font-semibold text-foreground">{stock.code}</span>
                            <span className="min-w-0 truncate text-xs text-secondary-text">{stock.name}</span>
                          </div>
                          <Badge className={cn('shrink-0 border-0 px-1.5 py-0.5 text-[10px]', marketClass(stock.market))}>{marketLabel(stock.market)}</Badge>
                          <span className="min-w-0 flex-1 truncate text-[11px] text-secondary-text">{stock.sector || '行业未标注'}</span>
                          <span className="shrink-0 text-[11px] text-muted-foreground">{stock.status === 'active' ? '正常' : stock.status || '未知'}</span>
                          <Button
                            variant={inWatchlist ? 'secondary' : 'outline'}
                            size="sm"
                            disabled={inWatchlist || busyAction === `add:${stock.code}`}
                            onClick={() => void addToDefaultWatchlist(stock.code)}
                            className={cn(COMPACT_BUTTON_CLASS, 'shrink-0')}
                          >
                            {inWatchlist ? <Check className="size-3" /> : <Plus className="size-3" />}
                            {inWatchlist ? '已在自选' : '加入自选'}
                          </Button>
                        </div>
                      );
                    })}
                  </div>
                </div>
                <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span>共 {marketData.total} 只 · 第 {marketData.page}/{Math.max(1, marketData.total_pages)} 页</span>
                  <div className="flex items-center gap-2">
                    {marketExportConfirming ? (
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] text-warning">确认导出 {marketData.total} 只股票？</span>
                        <Button
                          variant="ghost"
                          size="sm"
                          type="button"
                          onClick={() => setMarketExportConfirming(false)}
                          className={COMPACT_BUTTON_CLASS}
                        >
                          取消
                        </Button>
                        <Button
                          variant="primary"
                          size="sm"
                          type="button"
                          onClick={() => void exportAllMarketMarkdown()}
                          disabled={busyAction === 'export-market'}
                          className={COMPACT_BUTTON_CLASS}
                        >
                          确认导出
                        </Button>
                      </div>
                    ) : null}
                    <Button variant="secondary" size="sm" className={COMPACT_BUTTON_CLASS} disabled={marketPage <= 1} onClick={() => setMarketPage((page) => page - 1)}>
                      <ChevronLeft className="size-3" />上一页
                    </Button>
                    <Button variant="secondary" size="sm" className={COMPACT_BUTTON_CLASS} disabled={marketPage >= marketData.total_pages} onClick={() => setMarketPage((page) => page + 1)}>
                      下一页<ChevronRight className="size-3" />
                    </Button>
                  </div>
                </div>
              </>
            ) : (
              <EmptyState title="暂无股票数据" description="请先同步股票列表，或调整搜索和市场筛选条件。" className="border-dashed py-16" />
            )}
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col space-y-2.5 p-2.5 sm:p-3">
            <div className="shrink-0 rounded-xl border border-border/70 bg-elevated/25 p-2 shadow-sm">
              <div className="flex min-w-0 items-center gap-1 px-1">
                <span className={CONTROL_LABEL_CLASS}>分组：</span>
                <Controller
                  name="activeGroupId"
                  control={watchlistForm.control}
                  render={({ field }) => (
                    <CompactSelect
                      value={field.value}
                      ariaLabel="选择股票分组"
                      className="min-w-0 flex-1"
                      options={[
                        {
                          value: DEFAULT_GROUP_ID,
                          label: DEFAULT_GROUP_NAME,
                          count: watchlist?.count || 0,
                          showSelectedIndicator: false,
                        },
                        ...groups.map((group) => ({
                          value: group.id,
                          label: groupName(group),
                          count: group.codes.length,
                          showSelectedIndicator: false,
                          manageActions: true,
                        })),
                      ]}
                      onChange={(value) => {
                        field.onChange(value);
                        setExportConfirming(false);
                      }}
                      renderOptionActions={(option, close) => {
                        const group = groups.find((item) => item.id === option.value);
                        if (!group) return null;
                        return (
                          <>
                            <button
                              type="button"
                              aria-label={`编辑分组${groupName(group)}`}
                              title={`编辑分组 ${groupName(group)}`}
                              onClick={(event) => {
                                event.stopPropagation();
                                close();
                                watchlistForm.setValue('renameValue', groupName(group));
                                watchlistForm.clearErrors('renameValue');
                                setNotice(null);
                                setEditingGroupId(group.id);
                              }}
                              className="inline-flex size-5 items-center justify-center rounded text-muted-foreground transition hover:bg-cyan/10 hover:text-cyan"
                            >
                              <Pencil className="size-3" />
                            </button>
                            <button
                              type="button"
                              aria-label={`删除分组${groupName(group)}`}
                              title={`删除分组 ${groupName(group)}`}
                              onClick={(event) => {
                                event.stopPropagation();
                                close();
                                setDeleteTarget(group);
                              }}
                              disabled={busyAction === `delete-group:${group.id}`}
                              className="inline-flex size-5 items-center justify-center rounded text-muted-foreground transition hover:bg-danger/10 hover:text-danger disabled:opacity-50"
                            >
                              {busyAction === `delete-group:${group.id}`
                                ? <RefreshCw className="size-3 animate-spin" />
                                : <Trash2 className="size-3" />}
                            </button>
                          </>
                        );
                      }}
                    />
                  )}
                />
                <Button
                  variant="secondary"
                  size="sm"
                  type="button"
                  onClick={() => {
                    watchlistForm.setValue('newGroupName', '');
                    watchlistForm.clearErrors('newGroupName');
                    setNotice(null);
                    setCreateGroupOpen(true);
                  }}
                  className={COMPACT_BUTTON_CLASS}
                >
                  <FolderPlus className="size-3" />新建分组
                </Button>
              </div>
              <div className="relative mt-2 flex min-w-0 items-center gap-1 border-t border-border/50 px-1 pt-2">
                <span className={CONTROL_LABEL_CLASS}>搜索：</span>
                <div className="relative min-w-0 flex-1">
                  <Search className="pointer-events-none absolute left-2 top-1/2 size-3 -translate-y-1/2 text-muted-foreground" />
                  <input
                    {...watchlistForm.register('addQuery')}
                    placeholder="代码或名称，输入后自动查询"
                    aria-label="搜索股票"
                    autoComplete="off"
                    className="input-surface h-8 w-full rounded-md border pl-7 pr-7 !text-[11px] focus:outline-none"
                  />
                  {candidateLoading ? (
                    <RefreshCw className="pointer-events-none absolute right-2 top-1/2 size-3 -translate-y-1/2 animate-spin text-cyan" />
                  ) : watchlistValues.addQuery ? (
                    <button
                      type="button"
                      aria-label="清除搜索"
                      onClick={() => watchlistForm.setValue('addQuery', '')}
                      className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-full p-0.5 text-muted-foreground transition hover:bg-elevated hover:text-foreground"
                    >
                      <X className="size-3" />
                    </button>
                  ) : null}
                  {candidateItems.length ? (
                    <div className="absolute left-0 right-0 top-8 z-20 space-y-1 rounded-lg border border-border/60 bg-white p-1 shadow-lg">
                      {candidateItems.map((stock) => {
                        const alreadyInGroup = activeCodes.includes(stock.code);
                        return (
                          <button
                            key={stock.code}
                            type="button"
                            disabled={alreadyInGroup || busyAction === 'add-group'}
                            onClick={() => void addToActiveGroup([stock.code])}
                            className="flex w-full items-center gap-1.5 rounded-md border border-border/50 px-2 py-1 text-left text-[11px] transition hover:border-cyan/30 hover:bg-cyan/5 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            <span className="font-mono font-semibold text-foreground">{stock.code}</span>
                            <span className="truncate text-secondary-text">{stock.name}</span>
                            <span className="ml-auto text-muted-foreground">{alreadyInGroup ? '已在当前分组' : '加入'}</span>
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              </div>
            </div>

            {watchlistLoading ? (
              <div className="flex min-h-0 flex-1 items-center justify-center text-muted-foreground">
                <RefreshCw className="size-6 animate-spin text-cyan" />
              </div>
            ) : (
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border/60">
                {activeGroup ? (
                  <div className="flex shrink-0 border-b border-border/60 bg-elevated/35 px-2 py-1">
                    <form
                      onSubmit={(event) => {
                        event.preventDefault();
                        void submitBatchAdd(watchlistForm.getValues());
                      }}
                      className="flex w-full min-w-0 items-center gap-1 sm:max-w-sm"
                    >
                      <input
                        {...watchlistForm.register('batchInput')}
                        placeholder="批量代码，逗号/空格分隔"
                        aria-label="批量添加股票"
                        className="input-surface h-8 min-w-0 flex-1 rounded-md border px-2 !text-[11px] focus:outline-none"
                      />
                      <Button
                        variant="primary"
                        size="sm"
                        type="button"
                        onClick={() => { void submitBatchAdd(watchlistForm.getValues()); }}
                        disabled={!watchlistValues.batchInput?.trim() || busyAction === 'add-group'}
                        className={COMPACT_BUTTON_CLASS}
                      >
                        <Plus className="size-3" />添加
                      </Button>
                    </form>
                  </div>
                ) : null}
                {activeCodes.length ? (
                <div className="min-h-0 flex-1 grid grid-cols-2 gap-px overflow-y-auto bg-border/40 sm:grid-cols-3 lg:grid-cols-4">
                  {activeCodes.map((code) => {
                    const market = code.startsWith('688') ? 'kcb' : code.startsWith('6') ? 'sh' : code.startsWith('3') ? 'cyb' : code.startsWith('8') || code.startsWith('9') ? 'bj' : 'sz';
                    const name = stockNameByCode.get(code);
                    return (
                      <div key={code} className="group flex min-w-0 items-center gap-1.5 bg-white px-2.5 py-2 text-xs hover:bg-elevated/45">
                        <div className="flex min-w-0 items-center gap-1.5">
                          <span className="shrink-0 font-mono font-semibold text-foreground">{code}</span>
                          <span className="min-w-0 truncate text-secondary-text">{name || '未知股票'}</span>
                          <span className={cn('truncate rounded px-1.5 py-0.5 text-[10px]', marketClass(market))}>{marketLabel(market)}</span>
                        </div>
                          <button
                            type="button"
                            aria-label={`从${groupName(activeGroup)}移除${code}`}
                            title={`从${groupName(activeGroup)}移除 ${code}`}
                            disabled={busyAction === `remove:${code}`}
                            onClick={() => void removeFromActiveGroup(code)}
                            className="invisible ml-auto inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition hover:bg-danger/10 hover:text-danger group-hover:visible disabled:visible"
                          >
                            {busyAction === `remove:${code}` ? <RefreshCw className="size-3 animate-spin" /> : <Trash2 className="size-3.5" />}
                          </button>
                      </div>
                    );
                  })}
                </div>
                ) : (
                  <EmptyState title="当前分组暂无股票" description="在本分组上方批量添加，或使用搜索添加股票。" className="flex-1 border-0 py-16" />
                )}
                {activeGroup ? (
                  <div className="flex shrink-0 items-center justify-end border-t border-border/60 bg-white px-2 py-1">
                    {exportConfirming ? (
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] text-warning">确认导出 {activeCodeCount} 个代码？</span>
                        <Button
                          variant="ghost"
                          size="sm"
                          type="button"
                          onClick={() => setExportConfirming(false)}
                          className={COMPACT_BUTTON_CLASS}
                        >
                          取消
                        </Button>
                        <Button
                          variant="primary"
                          size="sm"
                          type="button"
                          onClick={exportActiveGroupMarkdown}
                          disabled={!activeCodeCount}
                          className={COMPACT_BUTTON_CLASS}
                        >
                          确认导出
                        </Button>
                      </div>
                    ) : (
                      <Button
                        variant="secondary"
                        size="sm"
                        type="button"
                        onClick={() => setExportConfirming(true)}
                        disabled={!activeCodeCount}
                        className={COMPACT_BUTTON_CLASS}
                      >
                        <Download className="size-3" />
                        导出 Markdown
                      </Button>
                    )}
                  </div>
                ) : null}
              </div>
            )}
          </div>
        )}
      </div>
  );
}
