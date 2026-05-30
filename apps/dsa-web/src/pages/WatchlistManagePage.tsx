import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Plus, Search, Settings, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { watchlistApi, type WatchlistResponse } from '../api/watchlist';
import { stocksApi, type StockMetaItem } from '../api/stocks';
import { Button, EmptyState, InlineAlert, Drawer } from '../components/common';
import { cn } from '../utils/cn';
import { classifyStock, MARKET_COLORS, MARKET_LABELS } from '../utils/market';
import {
  loadWatchlistGroups,
  saveWatchlistGroups,
  type WatchlistGroup,
} from '../utils/watchlistGroups';

const DEFAULT_GROUP_ID = 'default';
const DEFAULT_GROUP_NAME = '我的自选股';

const WatchlistManagePage: React.FC = () => {
  const navigate = useNavigate();

  // Watchlist data (API) — this IS the default group's source of truth
  const [watchlist, setWatchlist] = useState<WatchlistResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Groups (localStorage) — custom groups only; default group is implicit from API
  const [groups, setGroups] = useState<WatchlistGroup[]>([]);
  const [activeGroupId, setActiveGroupId] = useState<string>(DEFAULT_GROUP_ID);

  // Action loading states
  const [isAdding, setIsAdding] = useState(false);
  const [isBatchAdding, setIsBatchAdding] = useState(false);
  const [isBatchRemoving, setIsBatchRemoving] = useState(false);
  const [removingCodes, setRemovingCodes] = useState<Set<string>>(new Set());

  // Search / suggest — input uses ref to avoid re-render on every keystroke
  const addInputRef = useRef<HTMLInputElement | null>(null);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [suggestions, setSuggestions] = useState<StockMetaItem[]>([]);
  const [suggestOpen, setSuggestOpen] = useState(false);
  const suggestTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const suggestRef = useRef<HTMLDivElement | null>(null);

  // Manage drawer
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [batchInput, setBatchInput] = useState('');
  const [selectedCodes, setSelectedCodes] = useState<Set<string>>(new Set());

  // --- Data loading ---
  const loadWatchlist = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await watchlistApi.get();
      setWatchlist(result);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '加载自选股失败');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    document.title = '自选分组管理 - Stock-Signal-Desk';
    const loaded = loadWatchlistGroups();
    setGroups(loaded);
    void loadWatchlist();

    return () => {
      if (suggestTimerRef.current) clearTimeout(suggestTimerRef.current);
    };
  }, [loadWatchlist]);

  // persist groups whenever they change (skip initial load)
  const groupsInitialized = useRef(false);
  useEffect(() => {
    if (!groupsInitialized.current) {
      groupsInitialized.current = true;
      return;
    }
    saveWatchlistGroups(groups);
  }, [groups]);

  // Ensure activeGroupId is valid
  useEffect(() => {
    if (activeGroupId !== DEFAULT_GROUP_ID && !groups.some((g) => g.id === activeGroupId)) {
      setActiveGroupId(DEFAULT_GROUP_ID);
    }
  }, [groups, activeGroupId]);

  // --- Derived state ---
  const activeGroup = useMemo<WatchlistGroup | null>(() => {
    if (activeGroupId === DEFAULT_GROUP_ID) {
      return {
        id: DEFAULT_GROUP_ID,
        name: DEFAULT_GROUP_NAME,
        codes: watchlist?.codes || [],
      };
    }
    return groups.find((g) => g.id === activeGroupId) || null;
  }, [groups, activeGroupId, watchlist]);

  const displayCodes = useMemo(() => {
    if (!activeGroup) return [];
    return activeGroup.codes;
  }, [activeGroup]);

  const displayStocks = useMemo(() => {
    return displayCodes.map((code) => {
      const { market, marketLabel } = classifyStock(code);
      return { code, market, marketLabel };
    });
  }, [displayCodes]);

  const watchlistCodes = useMemo(() => new Set(watchlist?.codes || []), [watchlist]);

  // All group codes = default (API) ∪ custom groups
  const allGroupCodes = useMemo(() => {
    const codes = new Set(watchlist?.codes || []);
    for (const g of groups) {
      for (const c of g.codes) codes.add(c);
    }
    return codes;
  }, [groups, watchlist]);

  // --- Search / suggest (API on demand) ---
  const handleAddInputChange = useCallback(() => {
    const value = addInputRef.current?.value || '';
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
      } catch {
        setSuggestions([]);
        setSuggestOpen(false);
      } finally {
        setSuggestLoading(false);
      }
    }, 250);
  }, []);

  useEffect(() => {
    if (!suggestOpen) return;
    const handler = (e: MouseEvent) => {
      if (suggestRef.current && !suggestRef.current.contains(e.target as Node)) {
        setSuggestOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [suggestOpen]);

  // --- Actions ---
  const holdMessage = (msg: string) => {
    setSuccessMsg(msg);
    setTimeout(() => setSuccessMsg(null), 2000);
  };

  const handleAddStock = useCallback(async (code: string) => {
    setError(null);
    setIsAdding(true);
    try {
      const result = await watchlistApi.add([code]);
      setWatchlist({ codes: result.codes, count: result.count, configVersion: result.configVersion });

      // If in a custom group, also add to that group
      if (activeGroup && activeGroup.id !== DEFAULT_GROUP_ID) {
        setGroups((prev) =>
          prev.map((g) =>
            g.id === activeGroup.id && !g.codes.includes(code)
              ? { ...g, codes: [...g.codes, code] }
              : g,
          ),
        );
      }
      if (addInputRef.current) addInputRef.current.value = '';
      setSuggestOpen(false);
      setSuggestions([]);
      holdMessage(`已添加 ${code}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '添加失败');
    } finally {
      setIsAdding(false);
    }
  }, [activeGroup]);

  const handleRemoveFromGroup = useCallback((code: string) => {
    if (!activeGroup) return;

    if (activeGroup.id === DEFAULT_GROUP_ID) {
      // Default group: remove from API (syncs to stocks page) + custom groups
      setRemovingCodes((prev) => new Set(prev).add(code));
      void watchlistApi.remove([code])
        .then((result) => {
          setWatchlist({ codes: result.codes, count: result.count, configVersion: result.configVersion });
          // Also remove from custom groups
          setGroups((prev) =>
            prev.map((g) => ({
              ...g,
              codes: g.codes.filter((c) => c !== code),
            })),
          );
          holdMessage(`已移除 ${code}`);
        })
        .catch((err: unknown) => {
          setError(err instanceof Error ? err.message : '移除失败');
        })
        .finally(() => {
          setRemovingCodes((prev) => {
            const next = new Set(prev);
            next.delete(code);
            return next;
          });
        });
    } else {
      // Custom group: remove from group only
      setGroups((prev) =>
        prev.map((g) =>
          g.id === activeGroup.id
            ? { ...g, codes: g.codes.filter((c) => c !== code) }
            : g,
        ),
      );
      holdMessage(`已从分组移除 ${code}`);
    }
  }, [activeGroup]);

  // --- Group management ---
  const handleCreateGroup = useCallback(() => {
    const name = newGroupName.trim();
    if (!name) return;
    const newGroup: WatchlistGroup = {
      id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      name,
      codes: [],
    };
    setGroups((prev) => [...prev, newGroup]);
    setActiveGroupId(newGroup.id);
    setNewGroupName('');
    holdMessage(`已创建分组「${name}」`);
  }, [newGroupName]);

  const handleDeleteGroup = useCallback(() => {
    if (!activeGroup || activeGroup.id === DEFAULT_GROUP_ID) return;
    setGroups((prev) => prev.filter((g) => g.id !== activeGroup.id));
    setActiveGroupId(DEFAULT_GROUP_ID);
    holdMessage(`已删除分组「${activeGroup.name}」`);
  }, [activeGroup]);

  const handleRenameGroup = useCallback((name: string) => {
    if (!activeGroup || activeGroup.id === DEFAULT_GROUP_ID || !name.trim()) return;
    setGroups((prev) =>
      prev.map((g) => (g.id === activeGroup.id ? { ...g, name: name.trim() } : g)),
    );
    holdMessage(`已重命名为「${name.trim()}」`);
  }, [activeGroup]);

  // --- Sync watchlist: on mount, sync default group codes into custom groups ---
  const syncWatchlistRef = useRef(false);
  useEffect(() => {
    if (syncWatchlistRef.current || isLoading || !watchlist || groups.length === 0) return;
    syncWatchlistRef.current = true;
    // Clean orphaned API codes: in API but not in any group (default + custom)
    const allCodeSet = new Set<string>();
    for (const c of watchlist.codes) allCodeSet.add(c);
    for (const g of groups) {
      for (const c of g.codes) allCodeSet.add(c);
    }
    const orphans = watchlist.codes.filter((c) => !allCodeSet.has(c));
    if (orphans.length === 0) return;
    void watchlistApi.remove(orphans)
      .then((result) => {
        setWatchlist({ codes: result.codes, count: result.count, configVersion: result.configVersion });
      })
      .catch(() => { /* ignore */ });
  }, [isLoading, watchlist, groups]);

  const handleBatchAdd = useCallback(() => {
    const codes = batchInput.split(/[\n,，\s]+/).filter(Boolean);
    if (codes.length === 0 || !activeGroup) return;
    setIsBatchAdding(true);
    void watchlistApi.add(codes)
      .then((result) => {
        setWatchlist({ codes: result.codes, count: result.count, configVersion: result.configVersion });
        if (activeGroup.id !== DEFAULT_GROUP_ID) {
          setGroups((prev) =>
            prev.map((g) =>
              g.id === activeGroup.id
                ? { ...g, codes: Array.from(new Set([...g.codes, ...codes])) }
                : g,
            ),
          );
        }
        setBatchInput('');
        holdMessage(`已批量添加 ${codes.length} 只股票`);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : '批量添加失败');
      })
      .finally(() => setIsBatchAdding(false));
  }, [batchInput, activeGroup]);

  const handleBatchRemove = useCallback(() => {
    if (selectedCodes.size === 0 || !activeGroup) return;
    const codes = Array.from(selectedCodes);
    if (activeGroup.id === DEFAULT_GROUP_ID) {
      setIsBatchRemoving(true);
      void watchlistApi.remove(codes)
        .then((result) => {
          setWatchlist({ codes: result.codes, count: result.count, configVersion: result.configVersion });
          setGroups((prev) =>
            prev.map((g) => ({
              ...g,
              codes: g.codes.filter((c) => !codes.includes(c)),
            })),
          );
          setSelectedCodes(new Set());
          holdMessage(`已移除 ${codes.length} 只股票`);
        })
        .catch((err: unknown) => {
          setError(err instanceof Error ? err.message : '批量移除失败');
        })
        .finally(() => setIsBatchRemoving(false));
    } else {
      const removeSet = new Set(codes);
      setGroups((prev) =>
        prev.map((g) =>
          g.id === activeGroup.id
            ? { ...g, codes: g.codes.filter((c) => !removeSet.has(c)) }
            : g,
        ),
      );
      setSelectedCodes(new Set());
      holdMessage(`已从分组移出 ${codes.length} 只股票`);
    }
  }, [selectedCodes, activeGroup]);

  const toggleSelect = useCallback((code: string) => {
    setSelectedCodes((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }, []);

  // rename state for drawer
  const [renameValue, setRenameValue] = useState('');
  useEffect(() => {
    if (drawerOpen && activeGroup) setRenameValue(activeGroup.name);
  }, [drawerOpen, activeGroup]);

  if (isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
      </div>
    );
  }

  return (
    <div className="mx-auto flex h-[calc(100vh-2rem)] w-full max-w-[960px] flex-col gap-3 overflow-hidden px-3 py-4 sm:px-5">
      {/* Header — fixed at top */}
      <div className="shrink-0 flex items-center gap-4">
        <button
          type="button"
          onClick={() => navigate('/')}
          className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-600 transition hover:border-cyan-300 hover:text-cyan-700"
          aria-label="返回首页"
        >
          <ArrowLeft className="h-5 w-5" />
        </button>
        <div className="flex-1">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Portfolio</p>
          <h1 className="text-2xl font-semibold text-slate-950">自选分组管理</h1>
          <p className="mt-1 text-sm text-slate-500">管理股票分组，用于跑批分析时选择目标股票集合</p>
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
      </div>

      {/* Group tab bar — fixed at top */}
      <div className="shrink-0 flex items-center gap-1.5 overflow-x-auto rounded-2xl border border-slate-200 bg-white/88 px-3 py-2 shadow-sm">
        {/* Default group tab */}
        <button
          type="button"
          onClick={() => { setActiveGroupId(DEFAULT_GROUP_ID); setSelectedCodes(new Set()); }}
          className={cn(
            'flex shrink-0 items-center gap-1.5 rounded-xl px-3.5 py-2 text-sm font-medium transition whitespace-nowrap',
            activeGroupId === DEFAULT_GROUP_ID
              ? 'bg-cyan-50 text-cyan-800 ring-1 ring-cyan-200'
              : 'text-slate-600 hover:bg-slate-100',
          )}
        >
          {DEFAULT_GROUP_NAME}
          <span className={cn(
            'rounded-full px-1.5 py-0.5 text-[10px] font-normal',
            activeGroupId === DEFAULT_GROUP_ID ? 'bg-cyan-100 text-cyan-700' : 'bg-slate-100 text-slate-500',
          )}>
            {watchlist?.count || 0}
          </span>
        </button>

        {/* Custom group tabs */}
        {groups.map((group) => (
          <button
            key={group.id}
            type="button"
            onClick={() => { setActiveGroupId(group.id); setSelectedCodes(new Set()); }}
            className={cn(
              'flex shrink-0 items-center gap-1.5 rounded-xl px-3.5 py-2 text-sm font-medium transition whitespace-nowrap',
              activeGroupId === group.id
                ? 'bg-cyan-50 text-cyan-800 ring-1 ring-cyan-200'
                : 'text-slate-600 hover:bg-slate-100',
            )}
          >
            {group.name}
            <span className={cn(
              'rounded-full px-1.5 py-0.5 text-[10px] font-normal',
              activeGroupId === group.id ? 'bg-cyan-100 text-cyan-700' : 'bg-slate-100 text-slate-500',
            )}>
              {group.codes.length}
            </span>
          </button>
        ))}
        <button
          type="button"
          onClick={() => {
            const name = `分组${groups.length + 1}`;
            const newGroup: WatchlistGroup = {
              id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
              name,
              codes: [],
            };
            setGroups((prev) => [...prev, newGroup]);
            setActiveGroupId(newGroup.id);
          }}
          className="flex shrink-0 items-center gap-1 rounded-xl border border-dashed border-slate-300 px-3 py-2 text-sm text-slate-400 transition hover:border-cyan-300 hover:text-cyan-600"
        >
          <Plus className="h-4 w-4" />
          新建
        </button>
      </div>

      {/* Search + add bar — fixed at top */}
      <div className="shrink-0 flex gap-2">
        <div className="relative flex-1" ref={suggestRef}>
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            ref={addInputRef}
            type="text"
            onInput={handleAddInputChange}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !isAdding) {
                const val = addInputRef.current?.value?.trim();
                if (val) handleAddStock(val.split(/[,，\s]+/)[0]);
              }
            }}
            placeholder="搜索股票代码，加入当前分组..."
            disabled={isAdding}
            className="h-11 w-full rounded-xl border border-slate-200 bg-white pl-9 pr-4 text-sm text-slate-800 placeholder:text-slate-400 transition focus:border-cyan-400 focus:outline-none focus:ring-4 focus:ring-cyan-100 disabled:cursor-not-allowed disabled:opacity-60"
          />
          {/* Search loading */}
          {suggestLoading && (
            <div className="absolute right-3 top-1/2 -translate-y-1/2">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
            </div>
          )}
          {/* Search suggestions */}
          {suggestOpen && suggestions.length > 0 && (
            <div className="absolute z-50 mt-1 w-full rounded-xl border border-slate-200 bg-white shadow-lg">
              {suggestions.map((stock) => {
                const alreadyInGroup = activeGroupId === DEFAULT_GROUP_ID
                  ? watchlistCodes.has(stock.code)
                  : allGroupCodes.has(stock.code) || false;
                return (
                  <button
                    key={stock.code}
                    type="button"
                    onClick={() => { if (!alreadyInGroup) handleAddStock(stock.code); }}
                    disabled={alreadyInGroup}
                    className={cn(
                      'flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm transition first:rounded-t-xl last:rounded-b-xl',
                      alreadyInGroup
                        ? 'cursor-not-allowed opacity-50'
                        : 'hover:bg-cyan-50',
                    )}
                  >
                    <span className="font-mono font-medium text-slate-700">{stock.code}</span>
                    <span className="truncate text-slate-500">{stock.name}</span>
                    <span className={cn(
                      'ml-auto shrink-0 inline-flex rounded px-1.5 py-0.5 text-[10px] font-medium',
                      MARKET_COLORS[stock.market] || '',
                    )}>
                      {MARKET_LABELS[stock.market] || stock.market}
                    </span>
                    {alreadyInGroup && <span className="text-[10px] text-emerald-600 shrink-0">已添加</span>}
                  </button>
                );
              })}
            </div>
          )}
        </div>
        <Button
          variant="home-action-ai"
          size="sm"
          disabled={isAdding}
          onClick={() => {
            const val = addInputRef.current?.value?.trim();
            if (val) handleAddStock(val.split(/[,，\s]+/)[0]);
          }}
          className="h-11 shrink-0"
        >
          {isAdding ? (
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
          ) : (
            <Plus className="h-4 w-4" />
          )}
          添加
        </Button>
        <button
          type="button"
          onClick={() => { setDrawerOpen(true); setSelectedCodes(new Set()); }}
          className="inline-flex h-11 w-11 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-600 transition hover:border-cyan-300 hover:text-cyan-700"
          title="管理分组"
        >
          <Settings className="h-5 w-5" />
        </button>
      </div>

      {/* Stock card grid — fills remaining space, only scrollable area */}
      <div className="min-h-0 flex-1 overflow-y-auto rounded-2xl border border-slate-200 bg-white/88 shadow-sm">
        <div className="px-5 py-4">
          <div className="mb-3 flex items-center gap-2">
            <h2 className="text-sm font-semibold text-slate-800">
              {activeGroup?.name || DEFAULT_GROUP_NAME}
            </h2>
            <span className="rounded-full bg-cyan-100 px-2 py-0.5 text-[10px] font-medium text-cyan-700">
              {displayStocks.length} 只
            </span>
          </div>

          {displayStocks.length === 0 ? (
            <EmptyState
              title="暂无股票"
              description={activeGroupId === DEFAULT_GROUP_ID ? '在上方搜索并添加你的第一只自选股' : '搜索股票并添加到当前分组'}
              className="border-dashed py-12"
            />
          ) : (
            <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
              {displayStocks.map((stock) => {
                const isRemoving = removingCodes.has(stock.code);
                return (
                  <div
                    key={stock.code}
                    className={cn(
                      'group relative flex items-center justify-between rounded-lg border border-slate-100 bg-white px-3 py-2 text-xs transition hover:border-cyan-200 hover:bg-cyan-50/30',
                      isRemoving && 'opacity-50',
                    )}
                  >
                    <div
                      className="min-w-0 cursor-pointer flex-1"
                      onClick={() => navigate(`/analysis?symbol=${stock.code}`)}
                      title={`查看 ${stock.code} 分析`}
                    >
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono font-medium text-slate-700">{stock.code}</span>
                        <span className={cn(
                          'inline-flex rounded px-1.5 py-0.5 text-[10px] font-medium',
                          MARKET_COLORS[stock.market] || '',
                        )}>
                          {stock.marketLabel}
                        </span>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleRemoveFromGroup(stock.code)}
                      disabled={isRemoving}
                      className="invisible ml-1 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-slate-400 transition hover:bg-red-500 hover:text-white group-hover:visible disabled:visible disabled:cursor-wait"
                      title={`从${activeGroupId === DEFAULT_GROUP_ID ? '我的自选股' : '分组'}移除 ${stock.code}`}
                    >
                      {isRemoving ? (
                        <div className="h-3 w-3 animate-spin rounded-full border-[2px] border-red-200 border-t-red-500" />
                      ) : (
                        <X className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Manage Drawer */}
      <Drawer
        isOpen={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title="管理分组"
        width="max-w-lg"
      >
        <div className="space-y-6">
          {/* Default group info (read-only) */}
          {activeGroupId === DEFAULT_GROUP_ID && (
            <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-4">
              <h3 className="text-sm font-semibold text-slate-800">{DEFAULT_GROUP_NAME}</h3>
              <p className="text-xs text-slate-500">
                这是默认分组，与全市场股票页的"已添加"状态同步。在此分组中增删股票，会实时同步到 stocks 页面。不可改名或删除。
              </p>
            </div>
          )}

          {/* Rename / Delete group (custom groups only) */}
          {activeGroup && activeGroup.id !== DEFAULT_GROUP_ID && (
            <div className="space-y-3 rounded-xl border border-slate-200 bg-slate-50/70 p-4">
              <h3 className="text-sm font-semibold text-slate-800">当前分组：{activeGroup.name}</h3>
              <div className="flex gap-2">
                <input
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  placeholder="新名称"
                  className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-100"
                />
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!renameValue.trim() || renameValue === activeGroup.name}
                  onClick={() => handleRenameGroup(renameValue)}
                >
                  重命名
                </Button>
              </div>
              <Button
                variant="danger-subtle"
                size="sm"
                onClick={handleDeleteGroup}
              >
                删除此分组
              </Button>
            </div>
          )}

          {/* Create new group */}
          <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-4">
            <h3 className="text-sm font-semibold text-slate-800">新建分组</h3>
            <div className="flex gap-2">
              <input
                value={newGroupName}
                onChange={(e) => setNewGroupName(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleCreateGroup(); }}
                placeholder="分组名称"
                className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-100"
              />
              <Button
                variant="home-action-ai"
                size="sm"
                disabled={!newGroupName.trim()}
                onClick={handleCreateGroup}
              >
                创建
              </Button>
            </div>
          </div>

          {/* Batch add */}
          <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-4">
            <h3 className="text-sm font-semibold text-slate-800">批量添加股票</h3>
            <textarea
              value={batchInput}
              onChange={(e) => setBatchInput(e.target.value)}
              placeholder="粘贴股票代码，换行/逗号/空格分隔&#10;例如：&#10;600519&#10;300750&#10;000858"
              rows={4}
              className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm placeholder:text-slate-400 focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-100"
            />
            <Button
              variant="home-action-ai"
              size="sm"
              disabled={!batchInput.trim() || isBatchAdding}
              onClick={handleBatchAdd}
            >
              {isBatchAdding ? (
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
              {isBatchAdding ? '添加中...' : '确认添加'}
            </Button>
          </div>

          {/* Batch remove */}
          {displayStocks.length > 0 && (
            <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-4">
              <h3 className="text-sm font-semibold text-slate-800">
                从当前分组移出股票
              </h3>
              <div className="max-h-[180px] space-y-1 overflow-y-auto">
                {displayStocks.map((stock) => (
                  <button
                    key={stock.code}
                    type="button"
                    onClick={() => toggleSelect(stock.code)}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-lg border px-2.5 py-1.5 font-mono text-xs transition',
                      selectedCodes.has(stock.code)
                        ? 'border-red-300 bg-red-50 text-red-700'
                        : 'border-slate-200 bg-white text-slate-700 hover:border-cyan-200',
                    )}
                  >
                    {stock.code}
                    <span className="text-slate-400">{stock.marketLabel}</span>
                  </button>
                ))}
              </div>
              <Button
                variant="danger-subtle"
                size="sm"
                disabled={selectedCodes.size === 0 || isBatchRemoving}
                onClick={handleBatchRemove}
              >
                {isBatchRemoving ? (
                  <div className="h-4 w-4 animate-spin rounded-full border-2 border-red-200 border-t-red-500" />
                ) : null}
                {isBatchRemoving ? '移出中...' : `移出选中 (${selectedCodes.size})`}
              </Button>
            </div>
          )}
        </div>
      </Drawer>
    </div>
  );
};

export default WatchlistManagePage;
