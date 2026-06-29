import { Search, Plus, Settings } from 'lucide-react';
import { Button } from '../common';
import { cn } from '../../utils/cn';
import { MARKET_COLORS, MARKET_LABELS } from '../../utils/market';
import { DEFAULT_GROUP_ID } from '../../hooks/useWatchlistManage';

interface StockSuggestion {
  code: string;
  name: string;
  market: string;
}

interface StockSearchBarProps {
  suggestInputRef: React.RefObject<HTMLInputElement | null>;
  suggestContainerRef: React.RefObject<HTMLDivElement | null>;
  suggestLoading: boolean;
  suggestions: StockSuggestion[];
  suggestOpen: boolean;
  handleInputChange: () => void;
  isAdding: boolean;
  handleAddStock: (code: string) => void;
  watchlistCodes: Set<string>;
  allGroupCodes: Set<string>;
  activeGroupId: string;
  onOpenDrawer: () => void;
}

export default function StockSearchBar({
  suggestInputRef,
  suggestContainerRef,
  suggestLoading,
  suggestions,
  suggestOpen,
  handleInputChange,
  isAdding,
  handleAddStock,
  watchlistCodes,
  allGroupCodes,
  activeGroupId,
  onOpenDrawer,
}: StockSearchBarProps) {
  const isDefaultGroup = activeGroupId === DEFAULT_GROUP_ID;

  return (
    <div className="shrink-0 flex gap-2">
      <div className="relative flex-1" ref={suggestContainerRef}>
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
        <input
          ref={suggestInputRef}
          type="text"
          onInput={handleInputChange}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !isAdding) {
              const val = suggestInputRef.current?.value?.trim();
              if (val) handleAddStock(val.split(/[,，\s]+/)[0]);
            }
          }}
          placeholder="搜索股票代码，加入当前分组..."
          disabled={isAdding}
          className="h-11 w-full rounded-xl border border-slate-200 bg-white pl-9 pr-4 text-sm text-slate-800 placeholder:text-slate-400 transition focus:border-cyan-400 focus:outline-none focus:ring-4 focus:ring-cyan-100 disabled:cursor-not-allowed disabled:opacity-60"
        />
        {suggestLoading && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
          </div>
        )}
        {suggestOpen && suggestions.length > 0 && (
          <div className="absolute z-50 mt-1 w-full rounded-xl border border-slate-200 bg-white shadow-lg">
            {suggestions.map((stock) => {
              const alreadyInGroup = isDefaultGroup
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
        variant="primary"
        size="sm"
        disabled={isAdding}
        onClick={() => {
          const val = suggestInputRef.current?.value?.trim();
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
        onClick={onOpenDrawer}
        className="inline-flex h-11 w-11 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-600 transition hover:border-cyan-300 hover:text-cyan-700"
        aria-label="管理分组"
      >
        <Settings className="h-5 w-5" />
      </button>
    </div>
  );
}