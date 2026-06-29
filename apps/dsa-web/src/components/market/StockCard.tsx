import React from 'react';
import { Activity, Plus } from 'lucide-react';
import { cn } from '../../utils/cn';
import { MARKET_LABELS, MARKET_COLORS } from '../../utils/market';
import type { StockMetaItem } from '../../api/stocks';

interface StockCardProps {
  stock: StockMetaItem;
  isInWatchlist: boolean;
  onViewKline: (stock: { code: string; name: string }) => void;
  onAddStock: (code: string) => void;
  onNavigate: (path: string) => void;
}

export const StockCard: React.FC<StockCardProps> = ({ stock, isInWatchlist, onViewKline, onAddStock, onNavigate }) => (
  <div
    className={cn(
      'flex items-center justify-between rounded-lg border px-3 py-2 text-xs transition',
      isInWatchlist
        ? 'border-emerald-200 bg-emerald-50/50'
        : 'border-slate-100 bg-white hover:border-cyan-200 hover:bg-cyan-50/30',
    )}
  >
    <div
      className="min-w-0 flex-1 cursor-pointer"
      onClick={() => onNavigate(`/analysis?symbol=${stock.code}`)}
      title={`查看 ${stock.code} 分析`}
    >
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
    <div className="flex shrink-0 items-center gap-1">
      <button
        type="button"
        onClick={() => onViewKline({ code: stock.code, name: stock.name })}
        className="rounded-lg p-1.5 text-slate-400 transition hover:bg-cyan-100 hover:text-cyan-700"
        title={`查看 ${stock.code} K线`}
      >
        <Activity className="h-3.5 w-3.5" />
      </button>
      {isInWatchlist ? (
        <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
          已添加
        </span>
      ) : (
        <button
          type="button"
          onClick={() => onAddStock(stock.code)}
          className="rounded-lg p-1.5 text-slate-400 transition hover:bg-cyan-100 hover:text-cyan-700"
          title={`添加 ${stock.code}`}
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  </div>
);