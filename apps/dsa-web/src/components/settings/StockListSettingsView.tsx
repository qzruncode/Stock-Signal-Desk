import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Controller, useForm, useWatch } from 'react-hook-form';
import {
  Check,
  ChevronDown,
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
import { Badge, Button, ConfirmDialog, EmptyState, Modal } from '../common';
import { stocksApi, type StockMetaItem, type StocksListResponse } from '../../api/stocks';
import { watchlistApi, type WatchlistGroup, type WatchlistResponse } from '../../api/watchlist';
import { useStockIndex } from '../../hooks/useStockIndex';
import { cn } from '../../utils/cn';

type ViewMode = 'market' | 'watchlist';

interface MarketFormValues {
  search: string;
  market: string;
}

interface WatchlistFormValues {
  activeGroupId: string;
  newGroupName: string;
  renameValue: string;
  addQuery: string;
  batchInput: string;
}

interface CompactSelectOption {
  value: string;
  label: string;
  count?: number;
  showSelectedIndicator?: boolean;
  manageActions?: boolean;
}

const DEFAULT_GROUP_ID = 'default';
const DEFAULT_GROUP_NAME = '我的自选股';
const MARKET_OPTIONS = [
  { value: '', label: '全部市场' },
  { value: 'sh', label: '沪市主板' },
  { value: 'sz', label: '深市主板' },
  { value: 'cyb', label: '创业板' },
  { value: 'kcb', label: '科创板' },
  { value: 'bj', label: '北交所' },
];
const COMPACT_BUTTON_CLASS = 'h-7 gap-1 rounded-md px-2 text-[11px]';
const CONTROL_LABEL_CLASS = 'w-9 shrink-0 text-[11px] font-medium leading-4 text-secondary-text';

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

function readableError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function uniqueValues(values: string[]): string[] {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

function groupName(group: WatchlistGroup | null): string {
  const name = group?.name?.trim();
  return !name || name.toLowerCase() === 'null' || name.toLowerCase() === 'undefined'
    ? '未命名分组'
    : name;
}

function exportFileName(value: string): string {
  return value.replace(/[\\/:*?"<>|]/g, '-').trim() || '股票分组';
}

function CompactSelect({
  value,
  options,
  onChange,
  ariaLabel,
  className,
  renderOptionActions,
}: {
  value: string;
  options: CompactSelectOption[];
  onChange: (value: string) => void;
  ariaLabel: string;
  className?: string;
  renderOptionActions?: (option: CompactSelectOption, close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const selected = options.find((option) => option.value === value) ?? options[0];

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', closeOnOutsidePointer);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePointer);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [open]);

  return (
    <div ref={rootRef} className={cn('relative shrink-0', className)}>
      <button
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className={cn(
          'input-surface flex h-8 w-full min-w-[6.5rem] items-center justify-between gap-1.5 rounded-md border px-2 text-[11px] text-foreground transition',
          'hover:border-cyan/40 hover:bg-cyan/5 focus:outline-none focus:ring-2 focus:ring-cyan/15',
          open && 'border-cyan/50 bg-cyan/5',
        )}
      >
        <span className="truncate">{selected?.label ?? '请选择'}</span>
        <ChevronDown className={cn('size-3 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180 text-cyan')} />
      </button>
      {open ? (
        <div
          role="listbox"
          aria-label={ariaLabel}
          className="absolute right-0 top-[calc(100%+0.4rem)] z-50 min-w-full overflow-hidden rounded-xl border border-border/70 bg-white/95 p-1 shadow-[0_16px_40px_rgba(15,23,42,0.16)] backdrop-blur-xl"
        >
          {options.map((option) => {
            const selectedOption = option.value === value;
            return (
              <div
                key={option.value}
                role="option"
                aria-selected={selectedOption}
                className={cn(
                  'group relative flex w-full items-center gap-1 rounded-md px-1 transition',
                  selectedOption ? 'bg-cyan/10 text-cyan' : 'text-secondary-text',
                )}
              >
                <button
                  type="button"
                  onClick={() => {
                    onChange(option.value);
                    setOpen(false);
                  }}
                  className={cn(
                    'flex min-w-0 flex-1 items-center justify-between gap-2 rounded-md px-1.5 py-1.5 text-left text-[11px] transition',
                    selectedOption
                      ? 'font-medium text-cyan'
                      : 'hover:bg-elevated/70 hover:text-foreground',
                  )}
                >
                  <span className="truncate">{option.label}</span>
                  <span className={cn(
                    'flex shrink-0 items-center gap-1.5',
                    option.count !== undefined && 'ml-auto min-w-10 justify-end text-right',
                  )}>
                    {option.count !== undefined ? <span className="text-[10px] text-muted-foreground">{option.count}</span> : null}
                    {selectedOption && option.showSelectedIndicator !== false ? <Check className="size-3.5" /> : null}
                  </span>
                </button>
                {option.manageActions ? (
                  <div className="pointer-events-none absolute right-10 top-1/2 z-10 flex -translate-y-1/2 items-center gap-0 p-0 opacity-0 transition-opacity group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100">
                    {renderOptionActions?.(option, () => setOpen(false))}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

export function StockListSettingsView() {
  const [view, setView] = useState<ViewMode>('market');
  const [notice, setNotice] = useState<{ type: 'success' | 'error'; message: string } | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);

  const [marketSearch, setMarketSearch] = useState('');
  const [marketFilter, setMarketFilter] = useState('');
  const [marketPage, setMarketPage] = useState(1);
  const [marketData, setMarketData] = useState<StocksListResponse | null>(null);
  const [marketLoading, setMarketLoading] = useState(false);

  const [watchlist, setWatchlist] = useState<WatchlistResponse | null>(null);
  const [groups, setGroups] = useState<WatchlistGroup[]>([]);
  const [watchlistLoading, setWatchlistLoading] = useState(true);
  const [candidateItems, setCandidateItems] = useState<StockMetaItem[]>([]);
  const [candidateLoading, setCandidateLoading] = useState(false);
  const candidateRequestRef = useRef(0);
  const [createGroupOpen, setCreateGroupOpen] = useState(false);
  const [editingGroupId, setEditingGroupId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<WatchlistGroup | null>(null);
  const [pendingActiveGroupId, setPendingActiveGroupId] = useState<string | null>(null);
  const [exportConfirming, setExportConfirming] = useState(false);
  const { index: stockIndex } = useStockIndex();
  const stockNameByCode = useMemo(
    () => new Map(stockIndex.map((item) => [item.displayCode, item.nameZh])),
    [stockIndex],
  );
  const marketForm = useForm<MarketFormValues>({
    defaultValues: { search: '', market: '' },
  });
  const marketValues = useWatch({ control: marketForm.control });
  const watchlistForm = useForm<WatchlistFormValues>({
    defaultValues: {
      activeGroupId: DEFAULT_GROUP_ID,
      newGroupName: '',
      renameValue: '',
      addQuery: '',
      batchInput: '',
    },
  });
  const watchlistValues = useWatch({ control: watchlistForm.control });
  const activeGroupId = watchlistValues.activeGroupId || DEFAULT_GROUP_ID;

  useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(() => setNotice(null), 2800);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const loadMarket = useCallback(async () => {
    setMarketLoading(true);
    try {
      const result = await stocksApi.list({
        page: marketPage,
        page_size: 50,
        search: marketSearch || undefined,
        market: marketFilter || undefined,
      });
      setMarketData(result);
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '股票列表加载失败') });
    } finally {
      setMarketLoading(false);
    }
  }, [marketFilter, marketPage, marketSearch]);

  const loadWatchlist = useCallback(async () => {
    setWatchlistLoading(true);
    try {
      const [watchlistResult, groupResult] = await Promise.all([
        watchlistApi.get(),
        watchlistApi.listGroups(),
      ]);
      setWatchlist(watchlistResult);
      setGroups(groupResult);
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '自选股加载失败') });
    } finally {
      setWatchlistLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadMarket();
  }, [loadMarket]);

  useEffect(() => {
    void loadWatchlist();
  }, [loadWatchlist]);

  useEffect(() => {
    if (activeGroupId !== DEFAULT_GROUP_ID && !groups.some((group) => group.id === activeGroupId)) {
      watchlistForm.setValue('activeGroupId', DEFAULT_GROUP_ID);
    }
  }, [activeGroupId, groups, watchlistForm]);

  useEffect(() => {
    if (!pendingActiveGroupId || !groups.some((group) => group.id === pendingActiveGroupId)) return;
    watchlistForm.setValue('activeGroupId', pendingActiveGroupId);
    setPendingActiveGroupId(null);
  }, [groups, pendingActiveGroupId, watchlistForm]);

  const watchlistCodes = useMemo(() => new Set(watchlist?.codes || []), [watchlist]);
  const activeGroup = useMemo<WatchlistGroup | null>(() => {
    if (activeGroupId === DEFAULT_GROUP_ID) {
      return {
        id: DEFAULT_GROUP_ID,
        name: DEFAULT_GROUP_NAME,
        codes: watchlist?.codes || [],
      };
    }
    return groups.find((group) => group.id === activeGroupId) || null;
  }, [activeGroupId, groups, watchlist]);
  const activeCodes = useMemo(() => activeGroup?.codes || [], [activeGroup]);
  const activeCodeCount = uniqueValues(activeCodes).length;

  const exportActiveGroupMarkdown = useCallback(() => {
    setExportConfirming(false);
    if (!activeGroup) return;
    const codes = uniqueValues(activeCodes);
    if (!codes.length) {
      setNotice({ type: 'error', message: '当前分组没有可导出的股票代码。' });
      return;
    }
    const markdown = [
      `# ${groupName(activeGroup)}`,
      '',
      `股票数量：${codes.length}`,
      '',
      '## 股票代码',
      '',
      ...codes.map((code) => `- ${code}`),
      '',
    ].join('\n');
    const blobUrl = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = blobUrl;
    link.download = `${exportFileName(groupName(activeGroup))}-股票代码.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(blobUrl);
    setNotice({ type: 'success', message: `已导出「${groupName(activeGroup)}」的 ${codes.length} 个股票代码。` });
  }, [activeCodes, activeGroup]);

  const submitMarketSearch = useCallback((values: MarketFormValues) => {
    setMarketPage(1);
    setMarketSearch(values.search.trim());
  }, []);

  const addToDefaultWatchlist = useCallback(async (code: string) => {
    setBusyAction(`add:${code}`);
    setNotice(null);
    try {
      const result = await watchlistApi.add([code]);
      setWatchlist({
        codes: result.codes,
        count: result.count,
        configVersion: result.configVersion,
      });
      setNotice({ type: 'success', message: result.added.length ? `已加入自选股：${result.added.join('、')}` : `${code} 已在自选股中。` });
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '添加自选股失败') });
    } finally {
      setBusyAction(null);
    }
  }, []);

  const addToActiveGroup = useCallback(async (rawCodes: string[]) => {
    const codes = uniqueValues(rawCodes);
    if (!codes.length || !activeGroup) return;
    setBusyAction('add-group');
    setNotice(null);
    try {
      const result = await watchlistApi.add(codes);
      setWatchlist({
        codes: result.codes,
        count: result.count,
        configVersion: result.configVersion,
      });
      if (activeGroup.id === DEFAULT_GROUP_ID) {
        setNotice({ type: 'success', message: result.added.length ? `已添加 ${result.added.length} 只股票。` : '股票已经在默认自选股中。' });
      } else {
        const groupCodes = uniqueValues([...activeGroup.codes, ...(result.added.length ? result.added : codes)]);
        await watchlistApi.updateGroup(activeGroup.id, { codes: groupCodes });
        await loadWatchlist();
        setNotice({ type: 'success', message: `已添加到「${groupName(activeGroup)}」。` });
      }
      watchlistForm.setValue('addQuery', '');
      setCandidateItems([]);
      watchlistForm.setValue('batchInput', '');
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '添加股票失败') });
    } finally {
      setBusyAction(null);
    }
  }, [activeGroup, loadWatchlist, watchlistForm]);

  const findCandidates = useCallback(async (rawQuery: string) => {
    const query = rawQuery.trim();
    const requestId = candidateRequestRef.current + 1;
    candidateRequestRef.current = requestId;
    if (!query) {
      setCandidateItems([]);
      setCandidateLoading(false);
      return;
    }
    setCandidateLoading(true);
    setNotice(null);
    try {
      const result = await stocksApi.list({ page: 1, page_size: 10, search: query });
      if (requestId !== candidateRequestRef.current) return;
      setCandidateItems(result.items);
    } catch (error) {
      if (requestId !== candidateRequestRef.current) return;
      setNotice({ type: 'error', message: readableError(error, '股票搜索失败') });
    } finally {
      if (requestId === candidateRequestRef.current) setCandidateLoading(false);
    }
  }, []);

  useEffect(() => {
    const query = watchlistValues.addQuery || '';
    if (!query.trim()) {
      candidateRequestRef.current += 1;
      setCandidateItems([]);
      setCandidateLoading(false);
      return undefined;
    }
    const timer = window.setTimeout(() => {
      void findCandidates(query);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [findCandidates, watchlistValues.addQuery]);

  const removeFromActiveGroup = useCallback(async (code: string) => {
    if (!activeGroup) return;
    setBusyAction(`remove:${code}`);
    setNotice(null);
    try {
      if (activeGroup.id === DEFAULT_GROUP_ID) {
        const result = await watchlistApi.remove([code]);
        setWatchlist({
          codes: result.codes,
          count: result.count,
          configVersion: result.configVersion,
        });
        await Promise.all(
          groups
            .filter((group) => group.codes.includes(code))
            .map((group) => watchlistApi.updateGroup(group.id, {
              codes: group.codes.filter((item) => item !== code),
            })),
        );
        if (groups.some((group) => group.codes.includes(code))) await loadWatchlist();
      } else {
        await watchlistApi.updateGroup(activeGroup.id, {
          codes: activeGroup.codes.filter((item) => item !== code),
        });
        await loadWatchlist();
      }
      setNotice({ type: 'success', message: `已从「${groupName(activeGroup)}」移除 ${code}。` });
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '移除股票失败') });
    } finally {
      setBusyAction(null);
    }
  }, [activeGroup, groups, loadWatchlist]);

  const createGroup = useCallback(async (values: WatchlistFormValues) => {
    const name = values.newGroupName.trim();
    if (!name) return;
    if (groups.some((group) => group.name === name)) {
      setNotice({ type: 'error', message: `分组「${name}」已经存在。` });
      return;
    }
    setBusyAction('create-group');
    try {
      const created = await watchlistApi.createGroup(name);
      setPendingActiveGroupId(created.id);
      await loadWatchlist();
      watchlistForm.setValue('newGroupName', '');
      setCreateGroupOpen(false);
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '创建分组失败') });
    } finally {
      setBusyAction(null);
    }
  }, [groups, loadWatchlist, watchlistForm]);

  const renameGroup = useCallback(async (values: WatchlistFormValues) => {
    if (!editingGroupId) return;
    const group = groups.find((item) => item.id === editingGroupId);
    const name = values.renameValue.trim();
    if (!group || !name) return;
    if (groups.some((item) => item.id !== editingGroupId && groupName(item) === name)) {
      setNotice({ type: 'error', message: `分组「${name}」已经存在。` });
      return;
    }
    setBusyAction('rename-group');
    try {
      await watchlistApi.updateGroup(editingGroupId, { name });
      await loadWatchlist();
      setEditingGroupId(null);
      watchlistForm.setValue('renameValue', '');
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '分组重命名失败') });
    } finally {
      setBusyAction(null);
    }
  }, [editingGroupId, groups, loadWatchlist, watchlistForm]);

  const deleteGroup = useCallback(async (group: WatchlistGroup) => {
    if (group.id === DEFAULT_GROUP_ID) return;
    setBusyAction(`delete-group:${group.id}`);
    setNotice(null);
    try {
      await watchlistApi.deleteGroup(group.id);
      if (activeGroupId === group.id) watchlistForm.setValue('activeGroupId', DEFAULT_GROUP_ID);
      await loadWatchlist();
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '删除分组失败') });
    } finally {
      setBusyAction(null);
    }
  }, [activeGroupId, loadWatchlist, watchlistForm]);

  const closeEditGroup = useCallback(() => {
    if (busyAction === 'rename-group') return;
    setEditingGroupId(null);
    watchlistForm.setValue('renameValue', '');
    watchlistForm.clearErrors('renameValue');
  }, [busyAction, watchlistForm]);

  const submitBatchAdd = useCallback(async (values: WatchlistFormValues) => {
    await addToActiveGroup(values.batchInput.split(/[\n,，\s]+/));
  }, [addToActiveGroup]);

  return (
    <section className="flex h-full min-h-0 flex-col space-y-3 text-xs">
      {notice ? (
        <div className="pointer-events-none fixed inset-x-0 top-2 z-[70] flex justify-center px-3">
          <div
            role={notice.type === 'success' ? 'status' : 'alert'}
            aria-live="polite"
            className={cn(
              'flex max-w-[calc(100vw-1.5rem)] items-center gap-2 rounded-full border px-3 py-2 text-[11px] font-medium shadow-lg backdrop-blur-md',
              notice.type === 'success'
                ? 'border-success/25 bg-emerald-50/95 text-success'
                : 'border-danger/25 bg-red-50/95 text-danger',
            )}
          >
            {notice.type === 'success' ? <Check className="size-3.5 shrink-0" /> : <X className="size-3.5 shrink-0" />}
            <span className="truncate">{notice.message}</span>
          </div>
        </div>
      ) : null}

      <div className="terminal-card flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl">
        <div className="flex-none border-b border-border/60 px-3 py-2 sm:px-4">
          <div className="flex w-fit items-center gap-0.5 rounded-lg bg-elevated/70 p-0.5" role="tablist" aria-label="股票数据视图">
            <button
              type="button"
              role="tab"
              aria-selected={view === 'market'}
              onClick={() => setView('market')}
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
              onClick={() => setView('watchlist')}
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
                    onClick={() => { marketForm.setValue('search', ''); setMarketSearch(''); setMarketPage(1); }}
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
                    }}
                  />
                )}
              />
              <Button type="submit" variant="primary" size="sm" className={COMPACT_BUTTON_CLASS}>
                <Search className="size-3" />
                查询
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
                    <form onSubmit={watchlistForm.handleSubmit(submitBatchAdd)} className="flex w-full min-w-0 items-center gap-1 rounded-md bg-white p-0.5 shadow-sm sm:max-w-sm">
                      <input
                        {...watchlistForm.register('batchInput')}
                        placeholder="批量代码，逗号/空格分隔"
                        aria-label="批量添加股票"
                        className="input-surface h-8 min-w-0 flex-1 rounded-md border px-2 !text-[11px] focus:outline-none"
                      />
                      <Button
                        variant="primary"
                        size="sm"
                        type="submit"
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

      <ConfirmDialog
        isOpen={deleteTarget !== null}
        title="删除分组"
        message={`确定删除分组「${deleteTarget ? groupName(deleteTarget) : ''}」吗？其中的股票不会从默认自选股中删除。`}
        confirmText="删除分组"
        cancelText="取消"
        isDanger
        onConfirm={() => {
          if (!deleteTarget) return;
          void deleteGroup(deleteTarget);
          setDeleteTarget(null);
        }}
        onCancel={() => setDeleteTarget(null)}
      />

      <Modal
        isOpen={createGroupOpen}
        onClose={() => {
          if (busyAction !== 'create-group') {
            setCreateGroupOpen(false);
            watchlistForm.setValue('newGroupName', '');
            watchlistForm.clearErrors('newGroupName');
          }
        }}
        title="新建分组"
        width="max-w-md"
        preventClose={busyAction === 'create-group'}
        footer={(
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              type="button"
              onClick={() => {
                setCreateGroupOpen(false);
                watchlistForm.setValue('newGroupName', '');
                watchlistForm.clearErrors('newGroupName');
              }}
              disabled={busyAction === 'create-group'}
              className={COMPACT_BUTTON_CLASS}
            >
              取消
            </Button>
            <Button
              variant="primary"
              size="sm"
              type="submit"
              form="create-group-form"
              isLoading={busyAction === 'create-group'}
              loadingText="保存中..."
              className={COMPACT_BUTTON_CLASS}
            >
              保存
            </Button>
          </div>
        )}
      >
        <form id="create-group-form" onSubmit={watchlistForm.handleSubmit(createGroup)} className="space-y-2">
          <label htmlFor="new-group-name" className="text-[11px] font-medium text-foreground">分组名称</label>
          <input
            {...watchlistForm.register('newGroupName', { required: '请输入分组名称' })}
            id="new-group-name"
            autoFocus
            placeholder="例如：长期持仓"
            className="input-surface h-9 w-full rounded-md border px-2.5 text-xs focus:outline-none"
          />
          {watchlistForm.formState.errors.newGroupName?.message ? (
            <p className="text-xs text-danger">{watchlistForm.formState.errors.newGroupName.message}</p>
          ) : null}
        </form>
      </Modal>

      <Modal
        isOpen={editingGroupId !== null}
        onClose={closeEditGroup}
        title="编辑分组"
        width="max-w-md"
        preventClose={busyAction === 'rename-group'}
        footer={(
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              type="button"
              onClick={closeEditGroup}
              disabled={busyAction === 'rename-group'}
              className={COMPACT_BUTTON_CLASS}
            >
              取消
            </Button>
            <Button
              variant="primary"
              size="sm"
              type="submit"
              form="edit-group-form"
              isLoading={busyAction === 'rename-group'}
              loadingText="保存中..."
              className={COMPACT_BUTTON_CLASS}
            >
              保存
            </Button>
          </div>
        )}
      >
        <form id="edit-group-form" onSubmit={watchlistForm.handleSubmit(renameGroup)} className="space-y-2">
          <label htmlFor="edit-group-name" className="text-[11px] font-medium text-foreground">分组名称</label>
          <input
            {...watchlistForm.register('renameValue', { required: '请输入分组名称' })}
            id="edit-group-name"
            autoFocus
            className="input-surface h-9 w-full rounded-md border px-2.5 text-xs focus:outline-none"
          />
          {watchlistForm.formState.errors.renameValue?.message ? (
            <p className="text-xs text-danger">{watchlistForm.formState.errors.renameValue.message}</p>
          ) : null}
        </form>
      </Modal>
    </section>
  );
}

export default StockListSettingsView;
