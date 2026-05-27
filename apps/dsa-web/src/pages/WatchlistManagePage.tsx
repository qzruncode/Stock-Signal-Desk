import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Check, ChevronLeft, ChevronRight, Folder, FolderPlus, Plus, RefreshCw, Search, Trash2, Upload, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { watchlistApi, type WatchlistResponse } from '../api/watchlist';
import { stocksApi, type StockMetaItem, type SyncStatusResponse } from '../api/stocks';
import { Button, ConfirmDialog, EmptyState, InlineAlert } from '../components/common';
import { cn } from '../utils/cn';
import {
  loadWatchlistGroups,
  saveWatchlistGroups,
  type WatchlistGroup,
} from '../utils/watchlistGroups';

interface StockItem {
  code: string;
  market: string;
  marketLabel: string;
}

const MARKET_RULES: { prefix: string[]; key: string; label: string; color: string }[] = [
  { prefix: ['600', '601', '603', '605'], key: 'sh', label: '沪市主板', color: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' },
  { prefix: ['000', '001', '002', '003'], key: 'sz', label: '深市主板', color: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' },
  { prefix: ['300', '301'], key: 'cyb', label: '创业板', color: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400' },
  { prefix: ['688'], key: 'kcb', label: '科创板', color: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400' },
  { prefix: ['8', '9'], key: 'bj', label: '北交所', color: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400' },
];

function classifyStock(code: string): { market: string; marketLabel: string } {
  const upper = code.toUpperCase();
  if (upper.startsWith('HK')) return { market: 'hk', marketLabel: '港股' };
  if (/^[A-Z]+$/.test(upper) && upper.length <= 5) return { market: 'us', marketLabel: '美股' };
  for (const rule of MARKET_RULES) {
    for (const p of rule.prefix) {
      if (upper.startsWith(p)) return { market: rule.key, marketLabel: rule.label };
    }
  }
  return { market: 'other', marketLabel: '其他' };
}

const MARKET_GROUP_ORDER = ['sh', 'sz', 'cyb', 'kcb', 'bj', 'hk', 'us', 'other'];
const MARKET_LABELS: Record<string, string> = {
  sh: '沪市主板', sz: '深市主板', cyb: '创业板', kcb: '科创板', bj: '北交所', hk: '港股', us: '美股', other: '其他',
};
const MARKET_COLORS: Record<string, string> = {
  sh: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  sz: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  cyb: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
  kcb: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  bj: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400',
  hk: 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400',
  us: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400',
  other: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400',
};

const WatchlistManagePage: React.FC = () => {
  const navigate = useNavigate();
  const [data, setData] = useState<WatchlistResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Add form state
  const [addInput, setAddInput] = useState('');
  const [batchInput, setBatchInput] = useState('');
  const [showBatch, setShowBatch] = useState(false);
  const [isAdding, setIsAdding] = useState(false);

  // Remove state
  const [selectedCodes, setSelectedCodes] = useState<Set<string>>(new Set());
  const [showRemoveConfirm, setShowRemoveConfirm] = useState(false);
  const [isRemoving, setIsRemoving] = useState(false);
  const [groups, setGroups] = useState<WatchlistGroup[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<string>('');
  const [newGroupName, setNewGroupName] = useState('');

  // Sync state
  const [syncStatus, setSyncStatus] = useState<SyncStatusResponse | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Stock list state
  const [allStocks, setAllStocks] = useState<StockMetaItem[]>([]);
  const [stockTotal, setStockTotal] = useState(0);
  const [stockTotalPages, setStockTotalPages] = useState(1);
  const [stockPage, setStockPage] = useState(1);
  const [stockSearch, setStockSearch] = useState('');
  const [stockMarket, setStockMarket] = useState('');
  const [stockLoading, setStockLoading] = useState(false);

  // Search filter
  const [filter, setFilter] = useState('');

  // Add-stock suggestion dropdown (API-backed)
  const [suggestions, setSuggestions] = useState<StockMetaItem[]>([]);
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const suggestTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const suggestContainerRef = useRef<HTMLDivElement | null>(null);

  const loadWatchlist = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await watchlistApi.get();
      setData(result);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '加载自选股列表失败');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    document.title = '自选股管理 - Stock-Signal-Desk';
    setGroups(loadWatchlistGroups());
    void loadWatchlist();
    void loadSyncStatus();
    void loadStockList(1, '', '');

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [loadWatchlist]);

  // ---- Sync handlers ----
  const loadSyncStatus = useCallback(async () => {
    try {
      const status = await stocksApi.syncStatus();
      setSyncStatus(status);
      if (status.status === 'running') {
        // Poll every 2 seconds while running
        if (!pollRef.current) {
          pollRef.current = setInterval(async () => {
            try {
              const s = await stocksApi.syncStatus();
              setSyncStatus(s);
              if (s.status !== 'running') {
                if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
                if (s.status === 'success') {
                  setStockPage(1);
                  void loadStockList(1, stockSearch, stockMarket);
                }
              }
            } catch { /* ignore poll errors */ }
          }, 2000);
        }
      } else {
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      }
      return status;
    } catch { return null; }
  }, [stockSearch, stockMarket]);

  const loadStockList = useCallback(async (page: number, search: string, market: string) => {
    setStockLoading(true);
    try {
      const result = await stocksApi.list({ page, page_size: 50, search: search || undefined, market: market || undefined });
      setAllStocks(result.items);
      setStockTotal(result.total);
      setStockTotalPages(result.total_pages);
      setStockPage(result.page);
    } catch { /* ignore */ }
    finally { setStockLoading(false); }
  }, []);

  const handleSync = useCallback(async () => {
    if (isSyncing) return;
    setIsSyncing(true);
    setSyncError(null);
    try {
      const result = await stocksApi.sync();
      if (result.success) {
        await loadSyncStatus();
      }
    } catch (err: unknown) {
      setSyncError(err instanceof Error ? err.message : '同步启动失败');
    } finally {
      setIsSyncing(false);
    }
  }, [isSyncing, loadSyncStatus]);

  // ---- Stock list search ----
  const handleStockSearch = useCallback((value: string) => {
    setStockSearch(value);
    setStockPage(1);
    void loadStockList(1, value, stockMarket);
  }, [stockMarket, loadStockList]);

  const handleMarketFilter = useCallback((value: string) => {
    setStockMarket(value);
    setStockPage(1);
    void loadStockList(1, stockSearch, value);
  }, [stockSearch, loadStockList]);

  const handleStockPageChange = useCallback((page: number) => {
    if (page < 1 || page > stockTotalPages) return;
    void loadStockList(page, stockSearch, stockMarket);
  }, [stockSearch, stockMarket, stockTotalPages, loadStockList]);

  // ---- Add-stock suggestion search (API-backed) ----
  const handleAddInputChange = useCallback((value: string) => {
    setAddInput(value);
    if (suggestTimerRef.current) clearTimeout(suggestTimerRef.current);
    if (!value.trim()) {
      setSuggestions([]);
      setSuggestOpen(false);
      return;
    }
    suggestTimerRef.current = setTimeout(async () => {
      setSuggestLoading(true);
      try {
        const result = await stocksApi.list({ page: 1, page_size: 10, search: value.trim() });
        setSuggestions(result.items);
        setSuggestOpen(result.items.length > 0);
      } catch { setSuggestions([]); setSuggestOpen(false); }
      finally { setSuggestLoading(false); }
    }, 200);
  }, []);

  const handleSelectSuggestion = useCallback((code: string) => {
    setAddInput(code);
    setSuggestions([]);
    setSuggestOpen(false);
  }, []);

  // Close suggestion dropdown on outside click
  useEffect(() => {
    if (!suggestOpen) return;
    const handler = (e: MouseEvent) => {
      if (suggestContainerRef.current && !suggestContainerRef.current.contains(e.target as Node)) {
        setSuggestOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [suggestOpen]);

  // ---- Add from stock list to watchlist ----
  const handleAddStockFromList = useCallback(async (code: string) => {
    setError(null);
    setSuccessMsg(null);
    try {
      const result = await watchlistApi.add([code]);
      setData({ codes: result.codes, count: result.count, configVersion: result.configVersion });
      setSuccessMsg(`已添加 ${code}`);
      setTimeout(() => setSuccessMsg(null), 2000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '添加失败');
    }
  }, []);

  const persistGroups = useCallback((nextGroups: WatchlistGroup[]) => {
    setGroups(nextGroups);
    saveWatchlistGroups(nextGroups);
  }, []);

  const selectedGroup = useMemo(
    () => groups.find((group) => group.id === selectedGroupId) || groups[0] || null,
    [groups, selectedGroupId],
  );

  useEffect(() => {
    if (!selectedGroupId && groups.length > 0) {
      setSelectedGroupId(groups[0].id);
    }
    if (selectedGroupId && groups.length > 0 && !groups.some((group) => group.id === selectedGroupId)) {
      setSelectedGroupId(groups[0].id);
    }
  }, [groups, selectedGroupId]);

  const stocks = useMemo<StockItem[]>(() => {
    if (!data?.codes) return [];
    return data.codes.map((code) => {
      const { market, marketLabel } = classifyStock(code);
      return { code, market, marketLabel };
    });
  }, [data]);

  const marketGroups = useMemo(() => {
    const map = new Map<string, StockItem[]>();
    for (const s of stocks) {
      const list = map.get(s.market) || [];
      list.push(s);
      map.set(s.market, list);
    }
    return MARKET_GROUP_ORDER
      .filter((key) => map.has(key))
      .map((key) => ({
        key,
        label: MARKET_LABELS[key] || key,
        color: MARKET_COLORS[key] || '',
        items: map.get(key)!,
      }));
  }, [stocks]);

  const filteredGroups = useMemo(() => {
    if (!filter.trim()) return marketGroups;
    const f = filter.trim().toLowerCase();
    return marketGroups
      .map((g) => ({
        ...g,
        items: g.items.filter((item) => item.code.toLowerCase().includes(f) || item.marketLabel.includes(f)),
      }))
      .filter((g) => g.items.length > 0);
  }, [marketGroups, filter]);

  // ---- Add handlers ----
  const handleAddSingle = useCallback(async () => {
    const input = addInput.trim();
    if (!input) return;
    const codes = input.split(/[,，\s]+/).filter(Boolean);
    if (codes.length === 0) return;
    setIsAdding(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const result = await watchlistApi.add(codes);
      setData({ codes: result.codes, count: result.count, configVersion: result.configVersion });
      setAddInput('');
      setSuccessMsg(result.message);
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '添加失败');
    } finally {
      setIsAdding(false);
    }
  }, [addInput]);

  const handleAddBatch = useCallback(async () => {
    const input = batchInput.trim();
    if (!input) return;
    const codes = input.split(/[\n,，\s]+/).filter(Boolean);
    if (codes.length === 0) return;
    setIsAdding(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const result = await watchlistApi.add(codes);
      setData({ codes: result.codes, count: result.count, configVersion: result.configVersion });
      setBatchInput('');
      setShowBatch(false);
      setSuccessMsg(result.message);
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '添加失败');
    } finally {
      setIsAdding(false);
    }
  }, [batchInput]);

  // ---- Remove handlers ----
  const toggleSelect = useCallback((code: string) => {
    setSelectedCodes((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    const allCodes = new Set(stocks.map((s) => s.code));
    if (selectedCodes.size === allCodes.size) {
      setSelectedCodes(new Set());
    } else {
      setSelectedCodes(allCodes);
    }
  }, [stocks, selectedCodes.size]);

  const handleRemoveSelected = useCallback(async () => {
    if (selectedCodes.size === 0) return;
    setIsRemoving(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const result = await watchlistApi.remove(Array.from(selectedCodes));
      setData({ codes: result.codes, count: result.count, configVersion: result.configVersion });
      const removeSet = new Set(selectedCodes);
      persistGroups(groups.map((group) => ({
        ...group,
        codes: group.codes.filter((code) => !removeSet.has(code)),
      })));
      setSelectedCodes(new Set());
      setShowRemoveConfirm(false);
      setSuccessMsg(result.message);
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '删除失败');
    } finally {
      setIsRemoving(false);
    }
  }, [groups, persistGroups, selectedCodes]);

  const handleRemoveSingle = useCallback(async (code: string) => {
    setIsRemoving(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const result = await watchlistApi.remove([code]);
      setData({ codes: result.codes, count: result.count, configVersion: result.configVersion });
      persistGroups(groups.map((group) => ({
        ...group,
        codes: group.codes.filter((item) => item !== code),
      })));
      setSelectedCodes((prev) => {
        const next = new Set(prev);
        next.delete(code);
        return next;
      });
      setSuccessMsg(result.message);
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '删除失败');
    } finally {
      setIsRemoving(false);
    }
  }, [groups, persistGroups]);

  const handleCreateGroup = useCallback(() => {
    const name = newGroupName.trim();
    if (!name) return;
    const nextGroup: WatchlistGroup = {
      id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      name,
      codes: [],
    };
    persistGroups([...groups, nextGroup]);
    setSelectedGroupId(nextGroup.id);
    setNewGroupName('');
  }, [groups, newGroupName, persistGroups]);

  const handleAddSelectedToGroup = useCallback(() => {
    if (!selectedGroup || selectedCodes.size === 0) return;
    const selected = Array.from(selectedCodes);
    const nextGroups = groups.map((group) => (
      group.id === selectedGroup.id
        ? { ...group, codes: Array.from(new Set([...group.codes, ...selected])) }
        : group
    ));
    persistGroups(nextGroups);
  }, [groups, persistGroups, selectedCodes, selectedGroup]);

  const handleRemoveSelectedFromGroup = useCallback(() => {
    if (!selectedGroup || selectedCodes.size === 0) return;
    const removeSet = new Set(selectedCodes);
    persistGroups(groups.map((group) => (
      group.id === selectedGroup.id
        ? { ...group, codes: group.codes.filter((code) => !removeSet.has(code)) }
        : group
    )));
  }, [groups, persistGroups, selectedCodes, selectedGroup]);

  const handleDeleteGroup = useCallback(() => {
    if (!selectedGroup) return;
    const nextGroups = groups.filter((group) => group.id !== selectedGroup.id);
    persistGroups(nextGroups);
    setSelectedGroupId(nextGroups[0]?.id || '');
  }, [groups, persistGroups, selectedGroup]);

  if (isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-[960px] flex-col gap-6 px-3 py-6 sm:px-5">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={() => navigate('/')}
          className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-600 transition hover:border-cyan-300 hover:text-cyan-700"
          aria-label="返回首页"
        >
          <ArrowLeft className="h-5 w-5" />
        </button>
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Watchlist Manager</p>
          <h1 className="text-2xl font-semibold text-slate-950">自选股列表管理</h1>
          <p className="mt-1 text-sm text-slate-500">维护你的自选股票池，添加或删除关注的股票代码</p>
        </div>
      </div>

      {/* Alerts */}
      {error ? (
        <InlineAlert variant="danger" title="操作失败" message={error} className="rounded-xl px-3 py-2 text-xs shadow-none" />
      ) : null}
      {successMsg ? (
        <InlineAlert variant="success" title="操作成功" message={successMsg} className="rounded-xl px-3 py-2 text-xs shadow-none" />
      ) : null}
      {syncError ? (
        <InlineAlert variant="danger" title="同步失败" message={syncError} className="rounded-xl px-3 py-2 text-xs shadow-none" />
      ) : null}

      {/* Sync section */}
      <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-800">
              <RefreshCw className={cn('h-4 w-4 text-cyan-600', isSyncing && 'animate-spin')} />
              A 股全市场同步
            </h2>
            <p className="mt-1 text-xs text-slate-400">
              {syncStatus?.status === 'success'
                ? `最近同步: ${syncStatus.finished_at ? new Date(syncStatus.finished_at).toLocaleString() : '-'} | 共 ${syncStatus.total} 只 A 股`
                : syncStatus?.status === 'running'
                  ? `同步中... ${syncStatus.progress}/${syncStatus.total || '...'}`
                  : syncStatus?.status === 'failed'
                    ? `同步失败: ${syncStatus.error || syncStatus.message}`
                    : syncStatus?.status === 'idle' && syncStatus.total > 0
                      ? `上次同步: ${syncStatus.finished_at ? new Date(syncStatus.finished_at).toLocaleString() : '-'} | 共 ${syncStatus.total} 只`
                      : '尚未同步，点击按钮从东方财富同步全部 A 股数据'}
            </p>
          </div>
          <Button
            variant="home-action-ai"
            size="sm"
            disabled={isSyncing || syncStatus?.status === 'running'}
            onClick={handleSync}
            className="shrink-0"
          >
            {isSyncing || syncStatus?.status === 'running' ? (
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
            立即同步
          </Button>
        </div>
      </div>

      {/* Add section */}
      <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
        <div className="flex items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-800">
            <Plus className="h-4 w-4 text-emerald-600" />
            添加股票
          </h2>
          <button
            type="button"
            onClick={() => setShowBatch(!showBatch)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:border-cyan-300 hover:text-cyan-700"
          >
            <Upload className="h-3.5 w-3.5" />
            {showBatch ? '单条添加' : '批量添加'}
          </button>
        </div>

        {!showBatch ? (
          <div className="mt-3 flex gap-2">
            <div className="relative flex-1" ref={suggestContainerRef}>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <input
                  type="text"
                  value={addInput}
                  onChange={(e) => handleAddInputChange(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !isAdding && addInput.trim()) {
                      handleAddSingle();
                    }
                  }}
                  placeholder="搜索股票代码或名称，如 600519、贵州茅台"
                  disabled={isAdding}
                  className="h-11 w-full rounded-xl border border-slate-200 bg-white pl-9 pr-4 text-sm text-slate-800 placeholder:text-slate-400 transition focus:border-cyan-400 focus:outline-none focus:ring-4 focus:ring-cyan-100 disabled:cursor-not-allowed disabled:opacity-60"
                />
                {suggestLoading && (
                  <div className="absolute right-3 top-1/2 -translate-y-1/2">
                    <div className="h-4 w-4 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
                  </div>
                )}
              </div>
              {suggestOpen && suggestions.length > 0 && (
                <div className="absolute z-50 mt-1 w-full rounded-xl border border-slate-200 bg-white shadow-lg">
                  {suggestions.map((stock) => (
                    <button
                      key={stock.code}
                      type="button"
                      onClick={async () => {
                        handleSelectSuggestion(stock.code);
                        await handleAddStockFromList(stock.code);
                        setAddInput('');
                      }}
                      className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm transition first:rounded-t-xl last:rounded-b-xl hover:bg-cyan-50"
                    >
                      <span className="font-mono font-medium text-slate-700">{stock.code}</span>
                      <span className="truncate text-slate-500">{stock.name}</span>
                      <span className={cn(
                        'ml-auto shrink-0 inline-flex rounded px-1.5 py-0.5 text-[10px] font-medium',
                        MARKET_COLORS[stock.market] || '',
                      )}>
                        {MARKET_LABELS[stock.market] || stock.market}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <Button
              variant="home-action-ai"
              size="sm"
              disabled={!addInput.trim() || isAdding}
              onClick={handleAddSingle}
              className="h-11 shrink-0"
            >
              {isAdding ? (
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
              添加
            </Button>
          </div>
        ) : (
          <div className="mt-3 space-y-3">
            <textarea
              value={batchInput}
              onChange={(e) => setBatchInput(e.target.value)}
              placeholder="批量粘贴股票代码，用换行、逗号或空格分隔&#10;例如：&#10;600519&#10;300750&#10;002594,AAPL&#10;hk00700"
              rows={5}
              className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-800 placeholder:text-slate-400 focus:border-cyan-400 focus:outline-none focus:ring-4 focus:ring-cyan-100"
              disabled={isAdding}
            />
            <div className="flex items-center gap-2">
              <Button
                variant="home-action-ai"
                size="sm"
                disabled={!batchInput.trim() || isAdding}
                onClick={handleAddBatch}
              >
                {isAdding ? (
                  <div className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                ) : (
                  <Check className="h-4 w-4" />
                )}
                确认添加
              </Button>
              <button
                type="button"
                onClick={() => {
                  setBatchInput('');
                  setShowBatch(false);
                }}
                className="rounded-lg px-3 py-1.5 text-xs text-slate-500 transition hover:text-slate-700"
              >
                取消
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-800">
            <Folder className="h-4 w-4 text-cyan-600" />
            股票分组
          </h2>
          <div className="flex min-w-[260px] flex-1 justify-end gap-2">
            <input
              value={newGroupName}
              onChange={(event) => setNewGroupName(event.target.value)}
              placeholder="新分组名称"
              className="h-9 min-w-0 rounded-lg border border-slate-200 bg-slate-50 px-3 text-sm text-slate-800 placeholder:text-slate-400 focus:border-cyan-400 focus:outline-none focus:ring-4 focus:ring-cyan-100"
            />
            <Button
              variant="home-action-report"
              size="sm"
              disabled={!newGroupName.trim()}
              onClick={handleCreateGroup}
              className="shrink-0"
            >
              <FolderPlus className="h-4 w-4" />
              新建
            </Button>
          </div>
        </div>

        {groups.length === 0 ? (
          <p className="mt-3 text-xs text-slate-500">还没有分组。创建分组后，选中股票即可加入不同跑批范围。</p>
        ) : (
          <div className="mt-4 grid gap-4 lg:grid-cols-[220px_1fr]">
            <div className="space-y-1">
              {groups.map((group) => (
                <button
                  key={group.id}
                  type="button"
                  onClick={() => setSelectedGroupId(group.id)}
                  className={cn(
                    'flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm transition',
                    selectedGroup?.id === group.id
                      ? 'bg-cyan-50 text-cyan-800 ring-1 ring-cyan-200'
                      : 'text-slate-600 hover:bg-slate-50',
                  )}
                >
                  <span className="truncate">{group.name}</span>
                  <span className="rounded-full bg-white/70 px-2 py-0.5 text-[10px] text-slate-500">{group.codes.length}</span>
                </button>
              ))}
            </div>
            <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-slate-800">{selectedGroup?.name}</p>
                  <p className="text-xs text-slate-500">选中下方股票后，可加入或移出当前分组。首页跑批可按该分组执行。</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={!selectedGroup || selectedCodes.size === 0}
                    onClick={handleAddSelectedToGroup}
                  >
                    加入选中
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={!selectedGroup || selectedCodes.size === 0}
                    onClick={handleRemoveSelectedFromGroup}
                  >
                    移出选中
                  </Button>
                  <Button
                    variant="danger-subtle"
                    size="sm"
                    disabled={!selectedGroup}
                    onClick={handleDeleteGroup}
                  >
                    删除分组
                  </Button>
                </div>
              </div>
              <div className="mt-3 max-h-40 min-h-10 space-y-1 overflow-y-auto pr-1">
                {selectedGroup && selectedGroup.codes.length > 0 ? (
                  selectedGroup.codes.map((code) => (
                    <button
                      key={code}
                      type="button"
                      onClick={() => toggleSelect(code)}
                      className={cn(
                        'flex w-full items-center justify-between rounded-lg border px-2.5 py-1.5 font-mono text-xs transition',
                        selectedCodes.has(code)
                          ? 'border-red-300 bg-red-50 text-red-700'
                          : 'border-slate-200 bg-white text-slate-700 hover:border-cyan-300',
                      )}
                    >
                      <span>{code}</span>
                      {selectedCodes.has(code) ? <Check className="h-3 w-3" /> : null}
                    </button>
                  ))
                ) : (
                  <span className="text-xs text-slate-400">当前分组为空</span>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* A-share stock browser */}
      <div className="rounded-2xl border border-slate-200 bg-white/88 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-800">
            <Search className="h-4 w-4 text-indigo-600" />
            A 股全市场股票
            {syncStatus?.total ? (
              <span className="inline-flex items-center rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-700">
                {syncStatus.total} 只
              </span>
            ) : null}
          </h2>
        </div>

        {/* Search and filter bar */}
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-5 py-3">
          <div className="relative flex-1 min-w-[180px]">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              value={stockSearch}
              onChange={(e) => handleStockSearch(e.target.value)}
              placeholder="搜索股票代码或名称..."
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-8 text-sm text-slate-800 placeholder:text-slate-400 focus:border-cyan-400 focus:outline-none focus:ring-4 focus:ring-cyan-100"
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
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-cyan-400 focus:outline-none"
          >
            <option value="">全部市场</option>
            <option value="sh">沪市主板</option>
            <option value="sz">深市主板</option>
            <option value="cyb">创业板</option>
            <option value="kcb">科创板</option>
            <option value="bj">北交所</option>
          </select>
        </div>

        {/* Stock list */}
        <div className="max-h-[400px] overflow-y-auto px-5 py-4">
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
            <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
              {allStocks.map((stock) => {
                const isInWatchlist = data?.codes?.includes(stock.code);
                return (
                  <div
                    key={stock.code}
                    className={cn(
                      'flex items-center justify-between rounded-lg border px-3 py-2 text-xs transition',
                      isInWatchlist
                        ? 'border-emerald-200 bg-emerald-50/50'
                        : 'border-slate-100 bg-white hover:border-cyan-200 hover:bg-cyan-50/30',
                    )}
                  >
                    <div className="min-w-0 flex-1">
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
                    {isInWatchlist ? (
                      <span className="shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
                        已添加
                      </span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => handleAddStockFromList(stock.code)}
                        className="shrink-0 rounded-lg p-1.5 text-slate-400 transition hover:bg-cyan-100 hover:text-cyan-700"
                        title={`添加 ${stock.code}`}
                      >
                        <Plus className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Pagination */}
        {syncStatus && syncStatus.total > 0 && stockTotalPages > 1 && (
          <div className="flex items-center justify-between border-t border-slate-100 px-5 py-3">
            <span className="text-xs text-slate-400">
              共 {stockTotal} 只，第 {stockPage}/{stockTotalPages} 页
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                disabled={stockPage <= 1}
                onClick={() => handleStockPageChange(stockPage - 1)}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-xs text-slate-600 transition hover:border-cyan-300 disabled:opacity-40"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
                上一页
              </button>
              <button
                type="button"
                disabled={stockPage >= stockTotalPages}
                onClick={() => handleStockPageChange(stockPage + 1)}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-xs text-slate-600 transition hover:border-cyan-300 disabled:opacity-40"
              >
                下一页
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Stock list section */}
      <div className="rounded-2xl border border-slate-200 bg-white/88 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-800">
            <Search className="h-4 w-4 text-cyan-600" />
            当前自选股
            <span className="inline-flex items-center rounded-full bg-cyan-100 px-2 py-0.5 text-xs font-medium text-cyan-700">
              {stocks.length} 只
            </span>
          </h2>
          <div className="flex items-center gap-2">
            {/* Market summary pills */}
            <div className="hidden flex-wrap gap-1 sm:flex">
              {marketGroups.map((g) => (
                <span key={g.key} className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium', g.color)}>
                  {g.label} {g.items.length}
                </span>
              ))}
            </div>
            {stocks.length > 0 && (
              <div className="flex items-center gap-1.5 border-l border-slate-200 pl-2">
                <button
                  type="button"
                  onClick={toggleSelectAll}
                  className="rounded-lg px-2 py-1 text-xs text-slate-500 transition hover:text-cyan-700"
                >
                  {selectedCodes.size === stocks.length ? '取消全选' : '全选'}
                </button>
                {selectedCodes.size > 0 && (
                  <button
                    type="button"
                    onClick={() => setShowRemoveConfirm(true)}
                    disabled={isRemoving}
                    className="inline-flex items-center gap-1 rounded-lg border border-red-200 bg-red-50 px-3 py-1 text-xs font-medium text-red-600 transition hover:bg-red-100 disabled:opacity-50"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    删除 ({selectedCodes.size})
                  </button>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Search filter */}
        {stocks.length > 15 && (
          <div className="border-b border-slate-100 px-5 py-3">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                type="text"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="筛选股票代码..."
                className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-8 text-sm text-slate-800 placeholder:text-slate-400 focus:border-cyan-400 focus:outline-none focus:ring-4 focus:ring-cyan-100"
              />
              {filter && (
                <button
                  type="button"
                  onClick={() => setFilter('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                >
                  <X className="h-4 w-4" />
                </button>
              )}
            </div>
          </div>
        )}

        {/* Stock list */}
        <div className="max-h-[500px] overflow-y-auto px-5 py-4">
          {filteredGroups.length === 0 ? (
            <EmptyState
              title={stocks.length === 0 ? '还没有自选股' : '无匹配结果'}
              description={stocks.length === 0 ? '在上方输入股票代码添加你的第一只自选股' : '尝试调整筛选条件'}
              className="border-dashed py-12"
            />
          ) : (
            <div className="space-y-4">
              {filteredGroups.map((group) => (
                <div key={group.key}>
                  <div className="mb-2 flex items-center gap-2">
                    <span className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide', group.color)}>
                      {group.label}
                    </span>
                    <span className="text-[10px] text-slate-400">{group.items.length} 只</span>
                  </div>
                  <div className="space-y-1.5">
                    {group.items.map((item) => {
                      const isSelected = selectedCodes.has(item.code);
                      return (
                        <div
                          key={item.code}
                          className={cn(
                            'group flex items-center gap-3 rounded-xl border px-3 py-2 transition-colors',
                            isSelected
                              ? 'border-red-300 bg-red-50 text-red-700'
                              : 'border-slate-200 bg-white text-slate-700 hover:border-cyan-300 hover:bg-cyan-50',
                          )}
                        >
                          <button
                            type="button"
                            onClick={() => toggleSelect(item.code)}
                            className="flex min-w-0 flex-1 items-center gap-2 text-left"
                          >
                            {isSelected ? (
                              <Check className="h-3.5 w-3.5 shrink-0 text-red-500" />
                            ) : null}
                            <span className="truncate font-mono text-sm">{item.code}</span>
                            <span className="shrink-0 text-xs text-slate-400">{item.marketLabel}</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => handleRemoveSingle(item.code)}
                            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-slate-400 transition hover:bg-red-500 hover:text-white"
                            title={`删除 ${item.code}`}
                          >
                            <X className="h-3 w-3" />
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        {stocks.length > 0 && (
          <div className="border-t border-slate-100 px-5 py-3 text-center">
            <p className="text-[10px] text-slate-400">
              共 {stocks.length} 只自选股 |
              点击股票代码可选中进行批量删除 |
              悬停股票代码可快速删除
            </p>
          </div>
        )}
      </div>

      {/* Remove confirmation */}
      <ConfirmDialog
        isOpen={showRemoveConfirm}
        title="确认删除"
        message={
          selectedCodes.size === 1
            ? `确认从自选股列表中删除 "${Array.from(selectedCodes)[0]}" 吗？`
            : `确认删除选中的 ${selectedCodes.size} 只股票吗？删除后可通过重新添加恢复。`
        }
        confirmText={isRemoving ? '删除中...' : '确认删除'}
        cancelText="取消"
        isDanger={true}
        onConfirm={handleRemoveSelected}
        onCancel={() => setShowRemoveConfirm(false)}
      />
    </div>
  );
};

export default WatchlistManagePage;
