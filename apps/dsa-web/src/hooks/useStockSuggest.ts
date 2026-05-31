import { useCallback, useEffect, useRef, useState } from 'react';
import { stocksApi, type StockMetaItem } from '../api/stocks';

export function useStockSuggest(debounceMs = 250) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [loading, setLoading] = useState(false);
  const [suggestions, setSuggestions] = useState<StockMetaItem[]>([]);
  const [open, setOpen] = useState(false);

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const clear = useCallback(() => {
    clearTimer();
    setSuggestions([]);
    setOpen(false);
    if (inputRef.current) inputRef.current.value = '';
  }, [clearTimer]);

  const close = useCallback(() => {
    setOpen(false);
  }, []);

  const handleInputChange = useCallback(() => {
    const value = inputRef.current?.value.trim() || '';
    clearTimer();

    if (!value) {
      setSuggestions([]);
      setOpen(false);
      return;
    }

    timerRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const result = await stocksApi.list({ page: 1, page_size: 10, search: value });
        setSuggestions(result.items);
        setOpen(result.items.length > 0);
      } catch {
        setSuggestions([]);
        setOpen(false);
      } finally {
        setLoading(false);
        timerRef.current = null;
      }
    }, debounceMs);
  }, [clearTimer, debounceMs]);

  useEffect(() => clearTimer, [clearTimer]);

  useEffect(() => {
    if (!open) return;
    const handler = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return {
    inputRef,
    containerRef,
    loading,
    suggestions,
    open,
    clear,
    close,
    handleInputChange,
  };
}
