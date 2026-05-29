# Portfolio Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete dead code, extract shared market utils, and rewrite the portfolio page as a clean group manager with tabbed groups, card grid, and a manage drawer.

**Architecture:** Horizontal tab bar for group switching (like stock app sector tabs), responsive 3-column card grid matching MarketStocksPage style, search-with-autocomplete for adding stocks to current group, and a Drawer for batch management (rename/delete groups, batch add/remove).

**Tech Stack:** React, TypeScript, Tailwind CSS v4, existing project conventions

---

### Task 1: Delete dead code — WatchlistPanel.tsx

**Files:**
- Delete: `apps/dsa-web/src/components/dashboard/WatchlistPanel.tsx`

- [ ] **Step 1: Delete the file**

```bash
rm apps/dsa-web/src/components/dashboard/WatchlistPanel.tsx
```

- [ ] **Step 2: Verify no imports reference it**

```bash
grep -r "WatchlistPanel" apps/dsa-web/src --include="*.tsx" --include="*.ts"
```

Expected: no output (no remaining references)

- [ ] **Step 3: Commit**

```bash
git add apps/dsa-web/src/components/dashboard/WatchlistPanel.tsx
git commit -m "chore: remove unused WatchlistPanel component"
```

---

### Task 2: Extract shared market classification utility

**Files:**
- Create: `apps/dsa-web/src/utils/market.ts`
- Modify: `apps/dsa-web/src/pages/WatchlistManagePage.tsx`
- Modify: `apps/dsa-web/src/pages/MarketStocksPage.tsx`

- [ ] **Step 1: Create shared market utility**

Write `apps/dsa-web/src/utils/market.ts`:

```typescript
export interface MarketRule {
  prefix: string[];
  key: string;
  label: string;
  color: string;
}

export const MARKET_RULES: MarketRule[] = [
  { prefix: ['600', '601', '603', '605'], key: 'sh', label: '沪市主板', color: 'bg-red-100 text-red-700' },
  { prefix: ['000', '001', '002', '003'], key: 'sz', label: '深市主板', color: 'bg-blue-100 text-blue-700' },
  { prefix: ['300', '301'], key: 'cyb', label: '创业板', color: 'bg-purple-100 text-purple-700' },
  { prefix: ['688'], key: 'kcb', label: '科创板', color: 'bg-amber-100 text-amber-700' },
  { prefix: ['8', '9'], key: 'bj', label: '北交所', color: 'bg-emerald-100 text-emerald-700' },
];

export function classifyStock(code: string): { market: string; marketLabel: string } {
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

export const MARKET_GROUP_ORDER = ['sh', 'sz', 'cyb', 'kcb', 'bj', 'hk', 'us', 'other'];

export const MARKET_LABELS: Record<string, string> = {
  sh: '沪市主板', sz: '深市主板', cyb: '创业板', kcb: '科创板',
  bj: '北交所', hk: '港股', us: '美股', other: '其他',
};

export const MARKET_COLORS: Record<string, string> = {
  sh: 'bg-red-100 text-red-700', sz: 'bg-blue-100 text-blue-700',
  cyb: 'bg-purple-100 text-purple-700', kcb: 'bg-amber-100 text-amber-700',
  bj: 'bg-emerald-100 text-emerald-700', hk: 'bg-rose-100 text-rose-700',
  us: 'bg-indigo-100 text-indigo-700', other: 'bg-gray-100 text-gray-600',
};
```

- [ ] **Step 2: Verify the file was created**

```bash
head -5 apps/dsa-web/src/utils/market.ts
```

Expected: shows `export interface MarketRule {`

- [ ] **Step 3: Update MarketStocksPage to import from shared util**

In `apps/dsa-web/src/pages/MarketStocksPage.tsx`, replace the inline `MARKET_LABELS` and `MARKET_COLORS` objects (lines 11-23) with an import.

Remove lines 11-23:
```typescript
const MARKET_LABELS: Record<string, string> = {
  sh: '沪市主板', sz: '深市主板', cyb: '创业板', kcb: '科创板', bj: '北交所', hk: '港股', us: '美股', other: '其他',
};
const MARKET_COLORS: Record<string, string> = {
  sh: 'bg-red-100 text-red-700',
  sz: 'bg-blue-100 text-blue-700',
  cyb: 'bg-purple-100 text-purple-700',
  kcb: 'bg-amber-100 text-amber-700',
  bj: 'bg-emerald-100 text-emerald-700',
  hk: 'bg-rose-100 text-rose-700',
  us: 'bg-indigo-100 text-indigo-700',
  other: 'bg-gray-100 text-gray-600',
};
```

Add import at top of MarketStocksPage.tsx (after existing imports):
```typescript
import { MARKET_LABELS, MARKET_COLORS } from '../utils/market';
```

- [ ] **Step 4: Verify the page compiles**

```bash
cd apps/dsa-web && npx tsc --noEmit --pretty 2>&1 | head -30
```

Expected: no errors related to market.ts or MarketStocksPage

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/utils/market.ts apps/dsa-web/src/pages/MarketStocksPage.tsx
git commit -m "refactor: extract shared market classification to utils/market.ts"
```

---

### Task 3: Rewrite WatchlistManagePage — new group-based portfolio

**Files:**
- Modify: `apps/dsa-web/src/pages/WatchlistManagePage.tsx` (full rewrite)

This is the main task. The new page replaces all 6 sections with: tab bar, search bar, card grid, and manage drawer.

- [ ] **Step 1: Rewrite WatchlistManagePage.tsx**

Write `apps/dsa-web/src/pages/WatchlistManagePage.tsx`:

```typescript
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

const WatchlistManagePage: React.FC = () => {
  const navigate = useNavigate();

  // Watchlist data (API)
  const [watchlist, setWatchlist] = useState<WatchlistResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Groups (localStorage)
  const [groups, setGroups] = useState<WatchlistGroup[]>([]);
  const [activeGroupId, setActiveGroupId] = useState<string>('');

  // Search / suggest
  const [addInput, setAddInput] = useState('');
  const [isAdding, setIsAdding] = useState(false);
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
    if (loaded.length === 0) {
      const defaultGroup: WatchlistGroup = {
        id: 'all',
        name: '全部自选',
        codes: [],
      };
      setGroups([defaultGroup]);
    }
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
    if (groups.length > 0) saveWatchlistGroups(groups);
  }, [groups]);

  // Ensure activeGroupId is valid
  useEffect(() => {
    if (groups.length > 0 && !groups.some((g) => g.id === activeGroupId)) {
      setActiveGroupId(groups[0].id);
    }
  }, [groups, activeGroupId]);

  // --- Derived state ---
  const activeGroup = useMemo(
    () => groups.find((g) => g.id === activeGroupId) || groups[0] || null,
    [groups, activeGroupId],
  );

  const displayCodes = useMemo(() => {
    if (!activeGroup) return [];
    if (activeGroup.id === 'all') return watchlist?.codes || [];
    return activeGroup.codes;
  }, [activeGroup, watchlist]);

  const displayStocks = useMemo(() => {
    return displayCodes.map((code) => {
      const { market, marketLabel } = classifyStock(code);
      return { code, market, marketLabel };
    });
  }, [displayCodes]);

  const allWatchlistCodes = useMemo(() => new Set(watchlist?.codes || []), [watchlist]);

  // --- Search / suggest ---
  const handleAddInputChange = useCallback((value: string) => {
    setAddInput(value);
    if (suggestTimerRef.current) clearTimeout(suggestTimerRef.current);
    if (!value.trim()) {
      setSuggestions([]);
      setSuggestOpen(false);
      return;
    }
    suggestTimerRef.current = setTimeout(async () => {
      try {
        const result = await stocksApi.list({ page: 1, page_size: 8, search: value.trim() });
        setSuggestions(result.items);
        setSuggestOpen(result.items.length > 0);
      } catch {
        setSuggestions([]);
        setSuggestOpen(false);
      }
    }, 180);
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
      // Always add to global watchlist
      const result = await watchlistApi.add([code]);
      setWatchlist({ codes: result.codes, count: result.count, configVersion: result.configVersion });

      // Also add to active group if it's not "all"
      if (activeGroup && activeGroup.id !== 'all') {
        setGroups((prev) =>
          prev.map((g) =>
            g.id === activeGroup.id && !g.codes.includes(code)
              ? { ...g, codes: [...g.codes, code] }
              : g,
          ),
        );
      }
      setAddInput('');
      setSuggestOpen(false);
      holdMessage(`已添加 ${code}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '添加失败');
    } finally {
      setIsAdding(false);
    }
  }, [activeGroup]);

  const handleRemoveFromGroup = useCallback((code: string) => {
    if (!activeGroup) return;
    if (activeGroup.id === 'all') {
      // Remove from global watchlist
      void watchlistApi.remove([code]).then((result) => {
        setWatchlist({ codes: result.codes, count: result.count, configVersion: result.configVersion });
        holdMessage(`已移除 ${code}`);
      }).catch((err: unknown) => {
        setError(err instanceof Error ? err.message : '移除失败');
      });
    } else {
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
    if (!activeGroup || activeGroup.id === 'all') return;
    setGroups((prev) => prev.filter((g) => g.id !== activeGroup.id));
    setActiveGroupId('all');
    holdMessage(`已删除分组「${activeGroup.name}」`);
  }, [activeGroup]);

  const handleRenameGroup = useCallback((name: string) => {
    if (!activeGroup || activeGroup.id === 'all' || !name.trim()) return;
    setGroups((prev) =>
      prev.map((g) => (g.id === activeGroup.id ? { ...g, name: name.trim() } : g)),
    );
    holdMessage(`已重命名为「${name.trim()}」`);
  }, [activeGroup]);

  const handleBatchAdd = useCallback(() => {
    const codes = batchInput.split(/[\n,，\s]+/).filter(Boolean);
    if (codes.length === 0 || !activeGroup) return;
    void watchlistApi.add(codes).then((result) => {
      setWatchlist({ codes: result.codes, count: result.count, configVersion: result.configVersion });
      if (activeGroup.id !== 'all') {
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
    }).catch((err: unknown) => {
      setError(err instanceof Error ? err.message : '批量添加失败');
    });
  }, [batchInput, activeGroup]);

  const handleBatchRemove = useCallback(() => {
    if (selectedCodes.size === 0 || !activeGroup) return;
    const codes = Array.from(selectedCodes);
    if (activeGroup.id === 'all') {
      void watchlistApi.remove(codes).then((result) => {
        setWatchlist({ codes: result.codes, count: result.count, configVersion: result.configVersion });
        setSelectedCodes(new Set());
        holdMessage(`已移除 ${codes.length} 只股票`);
      }).catch((err: unknown) => {
        setError(err instanceof Error ? err.message : '批量移除失败');
      });
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
    <div className="mx-auto flex w-full max-w-[960px] flex-col gap-5 px-3 py-6 sm:px-5">
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
        <div className="flex-1">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Portfolio</p>
          <h1 className="text-2xl font-semibold text-slate-950">自选分组管理</h1>
          <p className="mt-1 text-sm text-slate-500">管理股票分组，用于跑批分析时选择目标股票集合</p>
        </div>
      </div>

      {/* Alerts */}
      {error ? (
        <InlineAlert variant="danger" title="操作失败" message={error} className="rounded-xl px-3 py-2 text-xs shadow-none" />
      ) : null}
      {successMsg ? (
        <InlineAlert variant="success" title="操作成功" message={successMsg} className="rounded-xl px-3 py-2 text-xs shadow-none" />
      ) : null}

      {/* Group tab bar */}
      <div className="flex items-center gap-1.5 overflow-x-auto rounded-2xl border border-slate-200 bg-white/88 px-3 py-2 shadow-sm">
        {groups.map((group) => (
          <button
            key={group.id}
            type="button"
            onClick={() => { setActiveGroupId(group.id); setSelectedCodes(new Set()); }}
            className={cn(
              'flex shrink-0 items-center gap-1.5 rounded-xl px-3.5 py-2 text-sm font-medium transition whitespace-nowrap',
              activeGroup?.id === group.id
                ? 'bg-cyan-50 text-cyan-800 ring-1 ring-cyan-200'
                : 'text-slate-600 hover:bg-slate-100',
            )}
          >
            {group.name}
            <span className={cn(
              'rounded-full px-1.5 py-0.5 text-[10px] font-normal',
              activeGroup?.id === group.id ? 'bg-cyan-100 text-cyan-700' : 'bg-slate-100 text-slate-500',
            )}>
              {group.id === 'all' ? (watchlist?.count || 0) : group.codes.length}
            </span>
          </button>
        ))}
        <button
          type="button"
          onClick={() => {
            const name = newGroupName.trim() || `分组${groups.length + 1}`;
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

      {/* Search + add bar */}
      <div className="flex gap-2">
        <div className="relative flex-1" ref={suggestRef}>
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            value={addInput}
            onChange={(e) => handleAddInputChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !isAdding && addInput.trim()) {
                handleAddStock(addInput.trim().split(/[,，\s]+/)[0]);
              }
            }}
            placeholder="搜索股票代码，加入当前分组..."
            disabled={isAdding}
            className="h-11 w-full rounded-xl border border-slate-200 bg-white pl-9 pr-4 text-sm text-slate-800 placeholder:text-slate-400 transition focus:border-cyan-400 focus:outline-none focus:ring-4 focus:ring-cyan-100 disabled:cursor-not-allowed disabled:opacity-60"
          />
          {suggestOpen && suggestions.length > 0 && (
            <div className="absolute z-50 mt-1 w-full rounded-xl border border-slate-200 bg-white shadow-lg">
              {suggestions.map((stock) => {
                const alreadyInGroup = activeGroup?.id === 'all'
                  ? allWatchlistCodes.has(stock.code)
                  : activeGroup?.codes.includes(stock.code) || false;
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
          disabled={!addInput.trim() || isAdding}
          onClick={() => handleAddStock(addInput.trim().split(/[,，\s]+/)[0])}
          className="h-11 shrink-0"
        >
          <Plus className="h-4 w-4" />
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

      {/* Stock card grid */}
      <div className="rounded-2xl border border-slate-200 bg-white/88 shadow-sm">
        <div className="px-5 py-4">
          <div className="mb-3 flex items-center gap-2">
            <h2 className="text-sm font-semibold text-slate-800">
              {activeGroup?.name || '全部自选'}
            </h2>
            <span className="rounded-full bg-cyan-100 px-2 py-0.5 text-[10px] font-medium text-cyan-700">
              {displayStocks.length} 只
            </span>
          </div>

          {displayStocks.length === 0 ? (
            <EmptyState
              title="暂无股票"
              description={activeGroup?.id === 'all' ? '在上方搜索并添加你的第一只自选股' : '搜索股票并添加到当前分组'}
              className="border-dashed py-12"
            />
          ) : (
            <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
              {displayStocks.map((stock) => (
                <div
                  key={stock.code}
                  className="group relative flex items-center justify-between rounded-lg border border-slate-100 bg-white px-3 py-2 text-xs transition hover:border-cyan-200 hover:bg-cyan-50/30"
                >
                  <div className="min-w-0 flex-1">
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
                    className="invisible ml-1 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-slate-400 transition hover:bg-red-500 hover:text-white group-hover:visible"
                    title={`从${activeGroup?.id === 'all' ? '自选股' : '分组'}移除 ${stock.code}`}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
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
          {/* Rename / Delete group */}
          {activeGroup && activeGroup.id !== 'all' && (
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
              disabled={!batchInput.trim()}
              onClick={handleBatchAdd}
            >
              确认添加
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
                disabled={selectedCodes.size === 0}
                onClick={handleBatchRemove}
              >
                移出选中 ({selectedCodes.size})
              </Button>
            </div>
          )}
        </div>
      </Drawer>
    </div>
  );
};

export default WatchlistManagePage;
```

- [ ] **Step 2: Type check the rewritten page**

```bash
cd apps/dsa-web && npx tsc --noEmit --pretty 2>&1 | head -30
```

Expected: no errors in WatchlistManagePage.tsx

- [ ] **Step 3: Verify the build compiles**

```bash
cd apps/dsa-web && npx vite build 2>&1 | tail -10
```

Expected: build succeeds

- [ ] **Step 4: Commit**

```bash
git add apps/dsa-web/src/pages/WatchlistManagePage.tsx
git commit -m "feat: redesign portfolio page as clean group manager with card grid"
```

---

### Task 4: Verify and finalize

**Files:**
- Modify: none (verification only)

- [ ] **Step 1: Run full type check**

```bash
cd apps/dsa-web && npx tsc --noEmit --pretty 2>&1 | tail -20
```

Expected: no errors

- [ ] **Step 2: Run existing tests**

```bash
cd apps/dsa-web && npx vitest run 2>&1 | tail -20
```

Expected: all tests pass (no new failures introduced)

- [ ] **Step 3: Check no references to deleted file remain**

```bash
grep -r "WatchlistPanel" apps/dsa-web/src 2>&1
```

Expected: no results

- [ ] **Step 4: Check no A-share sync/browse code left in portfolio page**

```bash
grep -n "stocksApi.sync\|syncStatus\|handleSync\|SyncStatusResponse" apps/dsa-web/src/pages/WatchlistManagePage.tsx 2>&1
```

Expected: no results (all moved to MarketStocksPage)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: final verification after portfolio redesign"
```
