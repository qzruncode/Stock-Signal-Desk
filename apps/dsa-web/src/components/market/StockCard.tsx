import React from 'react';
import { Activity, Info, Plus } from 'lucide-react';
import { cn } from '../../utils/cn';
import { MARKET_LABELS, MARKET_COLORS } from '../../utils/market';
import type { StockMetaItem } from '../../api/stocks';

interface StockCardProps {
  stock: StockMetaItem;
  isInWatchlist: boolean;
  onViewKline: (stock: { code: string; name: string }) => void;
  onViewDetails: (stock: StockMetaItem) => void;
  onAddStock: (code: string) => void;
  onNavigate: (path: string) => void;
}

function formatMarketCap(value: number | null) {
  return value != null ? `${(value / 1e8).toFixed(1)}亿` : '-';
}

function formatDateTime(value: string | null) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export const StockCard: React.FC<StockCardProps> = ({ stock, isInWatchlist, onViewKline, onViewDetails, onAddStock, onNavigate }) => {
  const metaLine = [
    stock.pe_ttm != null ? `PE ${stock.pe_ttm.toFixed(1)}` : null,
    stock.pb != null ? `PB ${stock.pb.toFixed(1)}` : null,
    stock.total_market_cap != null ? `总市值 ${formatMarketCap(stock.total_market_cap)}` : null,
    stock.circulating_market_cap != null ? `流通 ${formatMarketCap(stock.circulating_market_cap)}` : null,
  ].filter(Boolean);
  const profileLine = [
    stock.sector,
    stock.area,
    stock.ipo_date ? `上市 ${stock.ipo_date}` : null,
    stock.last_sync_at ? `同步 ${formatDateTime(stock.last_sync_at)}` : null,
  ].filter(Boolean);

  return (
    <div
      className={cn(
        'flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-xs transition',
        isInWatchlist
          ? 'border-success/30 bg-success/10'
          : 'border-border bg-card hover:border-primary/30 hover:bg-primary/5',
      )}
    >
      <div
        className="min-w-0 flex-1 cursor-pointer"
        onClick={() => onNavigate(`/analysis?symbol=${stock.code}`)}
        title={`查看 ${stock.code} 分析`}
      >
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="font-mono font-medium text-foreground">{stock.code}</span>
          <span className={cn(
            'inline-flex shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium',
            MARKET_COLORS[stock.market] || '',
          )}>
            {MARKET_LABELS[stock.market] || stock.market}
          </span>
          <span className="truncate text-muted-foreground">{stock.name}</span>
        </div>
        {metaLine.length > 0 && (
          <div className="mt-1 truncate text-[10px] text-muted-foreground/80">
            {metaLine.join(' | ')}
          </div>
        )}
        {profileLine.length > 0 && (
          <div className="mt-0.5 truncate text-[10px] text-muted-foreground/60">
            {profileLine.join(' | ')}
          </div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <button
          type="button"
          onClick={() => onViewDetails(stock)}
          className="rounded-lg p-1.5 text-muted-foreground transition hover:bg-primary/10 hover:text-primary"
          title={`查看 ${stock.code} 入库字段`}
        >
          <Info className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => onViewKline({ code: stock.code, name: stock.name })}
          className="rounded-lg p-1.5 text-muted-foreground transition hover:bg-primary/10 hover:text-primary"
          title={`查看 ${stock.code} K线`}
        >
          <Activity className="h-3.5 w-3.5" />
        </button>
        {isInWatchlist ? (
          <span className="rounded-full bg-success/15 px-2 py-0.5 text-[10px] font-medium text-success">
            已添加
          </span>
        ) : (
          <button
            type="button"
            onClick={() => onAddStock(stock.code)}
            className="rounded-lg p-1.5 text-muted-foreground transition hover:bg-primary/10 hover:text-primary"
            title={`添加 ${stock.code}`}
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </div>
  );
};
