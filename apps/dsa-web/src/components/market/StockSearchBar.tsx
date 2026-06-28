import React from 'react';
import { Search, X } from 'lucide-react';

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
  <div className="flex shrink-0 flex-wrap items-center gap-2 rounded-2xl border border-slate-200 bg-white/88 px-5 py-3 shadow-sm">
    <div className="relative min-w-[180px] flex-1">
      <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
      <input
        type="text"
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        placeholder="搜索股票代码或名称..."
        className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-8 text-sm text-slate-800 placeholder:text-slate-400 focus:border-indigo-400 focus:outline-none focus:ring-4 focus:ring-indigo-100"
      />
      {search && (
        <button
          type="button"
          onClick={() => onSearchChange('')}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
    <select
      value={market}
      onChange={(e) => onMarketChange(e.target.value)}
      className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-indigo-400 focus:outline-none"
    >
      {MARKET_OPTIONS.map((opt) => (
        <option key={opt.value} value={opt.value}>{opt.label}</option>
      ))}
    </select>
  </div>
);