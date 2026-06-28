import { X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { EmptyState } from '../common';
import { cn } from '../../utils/cn';
import { MARKET_COLORS } from '../../utils/market';
import type { DisplayStock } from '../../hooks/useWatchlistManage';

const DEFAULT_GROUP_NAME = '我的自选股';

interface StockCardGridProps {
  stocks: DisplayStock[];
  activeGroupName: string | null;
  isDefaultGroup: boolean;
  removingCodes: Set<string>;
  onRemove: (code: string) => void;
}

export default function StockCardGrid({
  stocks,
  activeGroupName,
  isDefaultGroup,
  removingCodes,
  onRemove,
}: StockCardGridProps) {
  const navigate = useNavigate();

  return (
    <div className="min-h-0 flex-1 overflow-y-auto rounded-2xl border border-slate-200 bg-white/88 shadow-sm">
      <div className="px-5 py-4">
        <div className="mb-3 flex items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-800">
            {activeGroupName || DEFAULT_GROUP_NAME}
          </h2>
          <span className="rounded-full bg-cyan-100 px-2 py-0.5 text-[10px] font-medium text-cyan-700">
            {stocks.length} 只
          </span>
        </div>

        {stocks.length === 0 ? (
          <EmptyState
            title="暂无股票"
            description={isDefaultGroup ? '在上方搜索并添加你的第一只自选股' : '搜索股票并添加到当前分组'}
            className="border-dashed py-12"
          />
        ) : (
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
            {stocks.map((stock) => {
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
                    onClick={() => onRemove(stock.code)}
                    disabled={isRemoving}
                    className="invisible ml-1 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-slate-400 transition hover:bg-red-500 hover:text-white group-hover:visible disabled:visible disabled:cursor-wait"
                    title={`从${isDefaultGroup ? '我的自选股' : '分组'}移除 ${stock.code}`}
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
  );
}