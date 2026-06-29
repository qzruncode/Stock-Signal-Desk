import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { watchlistApi, type WatchlistResponse } from '../api/watchlist';
import { classifyStock } from '../utils/market';
import {
  deleteWatchlistGroup,
  updateWatchlistGroup,
  upsertWatchlistGroup,
  type WatchlistGroup,
} from '../utils/watchlistGroups';
import { useWatchlistGroups } from './useWatchlistGroups';
import { useTransientMessage } from './useTransientMessage';
import { useStockSuggest } from './useStockSuggest';

export const DEFAULT_GROUP_ID = 'default';
export const DEFAULT_GROUP_NAME = '我的自选股';

export interface DisplayStock {
  code: string;
  market: string;
  marketLabel: string;
}

export function useWatchlistManage() {
  const [watchlist, setWatchlist] = useState<WatchlistResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { message: successMsg, showMessage: holdMessage } = useTransientMessage();
  const { groups } = useWatchlistGroups();
  const [activeGroupId, setActiveGroupId] = useState<string>(DEFAULT_GROUP_ID);
  const [isAdding, setIsAdding] = useState(false);
  const [isBatchAdding, setIsBatchAdding] = useState(false);
  const [isBatchRemoving, setIsBatchRemoving] = useState(false);
  const [removingCodes, setRemovingCodes] = useState<Set<string>>(new Set());
  const {
    inputRef: suggestInputRef,
    containerRef: suggestContainerRef,
    loading: suggestLoading,
    suggestions,
    open: suggestOpen,
    clear: clearSuggest,
    handleInputChange,
  } = useStockSuggest();
  const [newGroupName, setNewGroupName] = useState('');
  const [batchInput, setBatchInput] = useState('');
  const [selectedCodes, setSelectedCodes] = useState<Set<string>>(new Set());
  const abortRef = useRef<AbortController | null>(null);

  const loadWatchlist = useCallback(async () => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    setIsLoading(true);
    setError(null);
    try {
      const result = await watchlistApi.get(abortRef.current.signal);
      setWatchlist(result);
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      if (axios.isCancel(err)) return;
      setError(err instanceof Error ? err.message : '加载自选股失败');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadWatchlist();
    return () => { abortRef.current?.abort(); };
  }, [loadWatchlist]);

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

  const displayStocks = useMemo<DisplayStock[]>(
    () => displayCodes.map((code) => ({ code, ...classifyStock(code) })),
    [displayCodes],
  );

  const watchlistCodes = useMemo(() => new Set(watchlist?.codes || []), [watchlist]);

  // All group codes = default (API) ∪ custom groups; only exposed for suggestion
// deduplication, not for direct mutation.
  const allGroupCodes = useMemo(() => {
    const codes = new Set(watchlist?.codes || []);
    for (const g of groups) {
      for (const c of g.codes) codes.add(c);
    }
    return codes;
  }, [groups, watchlist]);

  // --- Actions ---
  const handleAddStock = useCallback(async (code: string) => {
    setError(null);
    setIsAdding(true);
    try {
      const result = await watchlistApi.add([code]);
      setWatchlist({ codes: result.codes, count: result.count, configVersion: result.configVersion });
      if (activeGroup && activeGroup.id !== DEFAULT_GROUP_ID && !activeGroup.codes.includes(code)) {
        await updateWatchlistGroup(activeGroup.id, { codes: [...activeGroup.codes, code] });
      }
      clearSuggest();
      holdMessage(`已添加 ${code}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '添加失败');
    } finally {
      setIsAdding(false);
    }
  }, [activeGroup, clearSuggest, holdMessage]);

  const handleRemoveFromGroup = useCallback((code: string) => {
    if (!activeGroup) return;

    if (activeGroup.id === DEFAULT_GROUP_ID) {
      setRemovingCodes((prev) => new Set(prev).add(code));
      void (async () => {
        try {
          const result = await watchlistApi.remove([code]);
          setWatchlist({ codes: result.codes, count: result.count, configVersion: result.configVersion });
          await Promise.all(
            groups
              .filter((g) => g.codes.includes(code))
              .map((g) => updateWatchlistGroup(g.id, { codes: g.codes.filter((c) => c !== code) })),
          );
          holdMessage(`已移除 ${code}`);
        } catch (err: unknown) {
          setError(err instanceof Error ? err.message : '移除失败');
        } finally {
          setRemovingCodes((prev) => {
            const next = new Set(prev);
            next.delete(code);
            return next;
          });
        }
      })();
    } else {
      void (async () => {
        try {
          await updateWatchlistGroup(activeGroup.id, { codes: activeGroup.codes.filter((c) => c !== code) });
          holdMessage(`已从分组移除 ${code}`);
        } catch (err: unknown) {
          setError(err instanceof Error ? err.message : '移除失败');
        }
      })();
    }
  }, [activeGroup, groups, holdMessage]);

  // --- Group management ---
  const handleCreateGroup = useCallback(async () => {
    const name = newGroupName.trim();
    if (!name) return;
    if (groups.some((g) => g.name === name)) {
      setError(`分组名称「${name}」已存在`);
      return;
    }
    try {
      const created = await upsertWatchlistGroup(name, []);
      setActiveGroupId(created.id);
      setNewGroupName('');
      holdMessage(`已创建分组「${name}」`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '创建分组失败');
    }
  }, [groups, holdMessage, newGroupName]);

  const handleQuickAddGroup = useCallback(async () => {
    const existing = new Set(groups.map((g) => g.name));
    let n = groups.length + 1;
    let name = `分组${n}`;
    while (existing.has(name)) {
      n += 1;
      name = `分组${n}`;
    }
    try {
      const created = await upsertWatchlistGroup(name, []);
      setActiveGroupId(created.id);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '创建分组失败');
    }
  }, [groups]);

  const handleDeleteGroup = useCallback(async () => {
    if (!activeGroup || activeGroup.id === DEFAULT_GROUP_ID) return;
    const name = activeGroup.name;
    try {
      await deleteWatchlistGroup(activeGroup.id);
      setActiveGroupId(DEFAULT_GROUP_ID);
      holdMessage(`已删除分组「${name}」`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '删除分组失败');
    }
  }, [activeGroup, holdMessage]);

  const handleRenameGroup = useCallback(async (name: string) => {
    if (!activeGroup || activeGroup.id === DEFAULT_GROUP_ID || !name.trim()) return;
    const trimmed = name.trim();
    try {
      await updateWatchlistGroup(activeGroup.id, { name: trimmed });
      holdMessage(`已重命名为「${trimmed}」`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '重命名失败');
    }
  }, [activeGroup, holdMessage]);

  const handleBatchAdd = useCallback(() => {
    const codes = batchInput.split(/[\n,，\s]+/).filter(Boolean);
    if (codes.length === 0 || !activeGroup) return;
    setIsBatchAdding(true);
    void (async () => {
      try {
        const result = await watchlistApi.add(codes);
        setWatchlist({ codes: result.codes, count: result.count, configVersion: result.configVersion });
        if (activeGroup.id !== DEFAULT_GROUP_ID) {
          await updateWatchlistGroup(activeGroup.id, {
            codes: Array.from(new Set([...activeGroup.codes, ...codes])),
          });
        }
        setBatchInput('');
        holdMessage(`已批量添加 ${codes.length} 只股票`);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : '批量添加失败');
      } finally {
        setIsBatchAdding(false);
      }
    })();
  }, [activeGroup, batchInput, holdMessage]);

  const handleBatchRemove = useCallback(() => {
    if (selectedCodes.size === 0 || !activeGroup) return;
    const codes = Array.from(selectedCodes);
    const removeSet = new Set(codes);
    if (activeGroup.id === DEFAULT_GROUP_ID) {
      setIsBatchRemoving(true);
      void (async () => {
        try {
          const result = await watchlistApi.remove(codes);
          setWatchlist({ codes: result.codes, count: result.count, configVersion: result.configVersion });
          await Promise.all(
            groups
              .filter((g) => g.codes.some((c) => removeSet.has(c)))
              .map((g) => updateWatchlistGroup(g.id, { codes: g.codes.filter((c) => !removeSet.has(c)) })),
          );
          setSelectedCodes(new Set());
          holdMessage(`已移除 ${codes.length} 只股票`);
        } catch (err: unknown) {
          setError(err instanceof Error ? err.message : '批量移除失败');
        } finally {
          setIsBatchRemoving(false);
        }
      })();
    } else {
      setIsBatchRemoving(true);
      void (async () => {
        try {
          await updateWatchlistGroup(activeGroup.id, {
            codes: activeGroup.codes.filter((c) => !removeSet.has(c)),
          });
          setSelectedCodes(new Set());
          holdMessage(`已从分组移出 ${codes.length} 只股票`);
        } catch (err: unknown) {
          setError(err instanceof Error ? err.message : '批量移除失败');
        } finally {
          setIsBatchRemoving(false);
        }
      })();
    }
  }, [activeGroup, groups, holdMessage, selectedCodes]);

  const toggleSelect = useCallback((code: string) => {
    setSelectedCodes((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }, []);

  // Rename state for drawer
  const [renameValue, setRenameValue] = useState('');

  const handleOpenDrawer = useCallback(() => {
    setSelectedCodes(new Set());
    if (activeGroup) setRenameValue(activeGroup.name);
  }, [activeGroup]);

  return {
    // State
    watchlist,
    isLoading,
    error,
    successMsg,
    holdMessage,
    groups,
    activeGroupId,
    isAdding,
    isBatchAdding,
    isBatchRemoving,
    removingCodes,
    newGroupName,
    batchInput,
    selectedCodes,
    renameValue,
    // StockSuggest
    suggestInputRef,
    suggestContainerRef,
    suggestLoading,
    suggestions,
    suggestOpen,
    handleInputChange,
    // Derived
    activeGroup,
    displayStocks,
    watchlistCodes,
    allGroupCodes,
    // Setters
    setError,
    setActiveGroupId,
    setNewGroupName,
    setBatchInput,
    setRenameValue,
    setSelectedCodes,
    // Actions
    handleAddStock,
    handleRemoveFromGroup,
    handleCreateGroup,
    handleQuickAddGroup,
    handleDeleteGroup,
    handleRenameGroup,
    handleBatchAdd,
    handleBatchRemove,
    toggleSelect,
    handleOpenDrawer,
  };
}