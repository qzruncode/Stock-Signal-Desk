import React from 'react';
import { ChevronDown, Search, X } from 'lucide-react';

interface StockSearchBarProps {
  search: string;
  market: string;
  onSearchChange: (value: string) => void;
  onMarketChange: (value: string) => void;
}

const MARKET_OPTIONS = [
  { value: '', label: '全部市场' },
  { value: 'sh', label: '沪市主板' },
  { value: 'sz', label: '深市主板' },
  { value: 'cyb', label: '创业板' },
  { value: 'kcb', label: '科创板' },
  { value: 'bj', label: '北交所' },
];

export const StockSearchBar: React.FC<StockSearchBarProps> = ({ search, market, onSearchChange, onMarketChange }) => (
  <div className="flex shrink-0 items-center gap-2 px-3 py-2 sm:px-4">
    <div className="relative flex-1">
      <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
      <input
        type="text"
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        placeholder="搜索代码或名称..."
        className="w-full rounded-md border border-border bg-muted/50 py-1.5 pl-8 pr-7 text-xs text-foreground placeholder:text-muted-foreground/60 focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/10"
      />
      {search && (
        <button
          type="button"
          onClick={() => onSearchChange('')}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
    <div className="relative">
      <select
        value={market}
        onChange={(e) => onMarketChange(e.target.value)}
        className="h-8 appearance-none rounded-md border border-border bg-card px-2.5 pr-7 py-0 text-xs text-foreground focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/10"
      >
        {MARKET_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-1.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
    </div>
  </div>
);