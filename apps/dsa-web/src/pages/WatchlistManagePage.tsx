import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Check, Folder, FolderPlus, Plus, Search, Trash2, Upload, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { watchlistApi, type WatchlistResponse } from '../api/watchlist';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { Button, ConfirmDialog, EmptyState, InlineAlert } from '../components/common';
import { cn } from '../utils/cn';

interface StockItem {
  code: string;
  market: string;
  marketLabel: string;
}

interface WatchlistGroup {
  id: string;
  name: string;
  codes: string[];
}

const WATCHLIST_GROUPS_STORAGE_KEY = 'dsa.watchlist.groups.v1';

function loadWatchlistGroups(): WatchlistGroup[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(WATCHLIST_GROUPS_STORAGE_KEY) || '[]') as WatchlistGroup[];
    return Array.isArray(parsed)
      ? parsed.filter((group) => group && group.id && group.name && Array.isArray(group.codes))
      : [];
  } catch {
    return [];
  }
}

function saveWatchlistGroups(groups: WatchlistGroup[]) {
  window.localStorage.setItem(WATCHLIST_GROUPS_STORAGE_KEY, JSON.stringify(groups));
  window.dispatchEvent(new Event('dsa-watchlist-groups-updated'));
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

  // Search filter
  const [filter, setFilter] = useState('');

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
    document.title = '自选股管理 - DSA';
    setGroups(loadWatchlistGroups());
    void loadWatchlist();
  }, [loadWatchlist]);

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

  const handleAutocompleteSubmit = useCallback(
    (stockCode: string) => {
      setAddInput(stockCode);
      // auto-submit after a tick to allow state to settle
      setTimeout(() => {
        watchlistApi.add([stockCode]).then((result) => {
          setData({ codes: result.codes, count: result.count, configVersion: result.configVersion });
          setAddInput('');
          setSuccessMsg(result.message);
          setTimeout(() => setSuccessMsg(null), 3000);
        }).catch((err: unknown) => {
          setError(err instanceof Error ? err.message : '添加失败');
        });
      }, 50);
    },
    [],
  );

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
            <div className="flex-1">
              <StockAutocomplete
                value={addInput}
                onChange={setAddInput}
                onSubmit={handleAutocompleteSubmit}
                placeholder="输入股票代码或名称，如 600519、贵州茅台、AAPL"
                disabled={isAdding}
              />
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
              <div className="mt-3 flex min-h-10 flex-wrap gap-1.5">
                {selectedGroup && selectedGroup.codes.length > 0 ? (
                  selectedGroup.codes.map((code) => (
                    <button
                      key={code}
                      type="button"
                      onClick={() => toggleSelect(code)}
                      className={cn(
                        'rounded-lg border px-2.5 py-1.5 font-mono text-xs transition',
                        selectedCodes.has(code)
                          ? 'border-red-300 bg-red-50 text-red-700'
                          : 'border-slate-200 bg-white text-slate-700 hover:border-cyan-300',
                      )}
                    >
                      {code}
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
                  <div className="flex flex-wrap gap-1.5">
                    {group.items.map((item) => {
                      const isSelected = selectedCodes.has(item.code);
                      return (
                        <div key={item.code} className="group relative">
                          <button
                            type="button"
                            onClick={() => toggleSelect(item.code)}
                            className={cn(
                              'inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-mono transition-all',
                              isSelected
                                ? 'border-red-300 bg-red-50 text-red-700'
                                : 'border-slate-200 bg-white text-slate-700 hover:border-cyan-300 hover:bg-cyan-50',
                            )}
                          >
                            {isSelected ? (
                              <Check className="h-3 w-3 text-red-500" />
                            ) : null}
                            {item.code}
                          </button>
                          <button
                            type="button"
                            onClick={() => handleRemoveSingle(item.code)}
                            className="absolute -top-1.5 -right-1.5 hidden h-5 w-5 items-center justify-center rounded-full bg-red-500 text-white shadow-sm transition hover:bg-red-600 group-hover:inline-flex"
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
