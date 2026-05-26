import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Copy, ExternalLink, Search, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { systemConfigApi } from '../../api/systemConfig';
import { cn } from '../../utils/cn';

interface StockItem {
  code: string;
  market: string;
  marketLabel: string;
}

interface MarketGroup {
  key: string;
  label: string;
  color: string;
  items: StockItem[];
}

const MARKET_RULES: { prefix: string[]; key: string; label: string; color: string }[] = [
  { prefix: ['600', '601', '603', '605'], key: 'sh', label: '沪市主板', color: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' },
  { prefix: ['000', '001', '002', '003'], key: 'sz', label: '深市主板', color: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' },
  { prefix: ['300', '301'], key: 'cyb', label: '创业板', color: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400' },
  { prefix: ['688'], key: 'kcb', label: '科创板', color: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400' },
  { prefix: ['8', '9'], key: 'bj', label: '北交所', color: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400' },
];

function classifyStock(code: string): { market: string; marketLabel: string } {
  const upper = code.toUpperCase();

  if (upper.startsWith('HK')) {
    return { market: 'hk', marketLabel: '港股' };
  }
  if (/^[A-Z]+$/.test(upper) && upper.length <= 5) {
    return { market: 'us', marketLabel: '美股' };
  }

  for (const rule of MARKET_RULES) {
    for (const p of rule.prefix) {
      if (upper.startsWith(p)) {
        return { market: rule.key, marketLabel: rule.label };
      }
    }
  }

  return { market: 'other', marketLabel: '其他' };
}

interface WatchlistPanelProps {
  onSelectStock?: (code: string) => void;
  className?: string;
}

export const WatchlistPanel: React.FC<WatchlistPanelProps> = ({ onSelectStock, className }) => {
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(true);
  const [stocks, setStocks] = useState<StockItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [copiedCode, setCopiedCode] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    systemConfigApi.getConfig(false)
      .then((config) => {
        if (!active) return;
        const stockListItem = config.items.find((item) => item.key === 'STOCK_LIST');
        const raw = stockListItem?.value || '';
        const codes = raw
          .split(',')
          .map((c) => c.trim())
          .filter(Boolean);
        const items: StockItem[] = codes.map((code) => {
          const { market, marketLabel } = classifyStock(code);
          return { code, market, marketLabel };
        });
        setStocks(items);
        setIsLoading(false);
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : '加载失败');
        setIsLoading(false);
      });
    return () => { active = false; };
  }, []);

  const marketGroups = useMemo<MarketGroup[]>(() => {
    const groupOrder = ['sh', 'sz', 'cyb', 'kcb', 'bj', 'hk', 'us', 'other'];
    const groupLabels: Record<string, string> = {
      sh: '沪市主板', sz: '深市主板', cyb: '创业板', kcb: '科创板', bj: '北交所', hk: '港股', us: '美股', other: '其他',
    };
    const groupColors: Record<string, string> = {
      sh: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
      sz: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
      cyb: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
      kcb: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
      bj: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400',
      hk: 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400',
      us: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400',
      other: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400',
    };
    const map = new Map<string, StockItem[]>();
    for (const s of stocks) {
      const list = map.get(s.market) || [];
      list.push(s);
      map.set(s.market, list);
    }
    return groupOrder
      .filter((key) => map.has(key))
      .map((key) => ({
        key,
        label: groupLabels[key] || key,
        color: groupColors[key] || '',
        items: map.get(key)!,
      }));
  }, [stocks]);

  const filteredGroups = useMemo(() => {
    if (!filter.trim()) return marketGroups;
    const f = filter.trim().toLowerCase();
    return marketGroups
      .map((g) => ({
        ...g,
        items: g.items.filter(
          (item) =>
            item.code.toLowerCase().includes(f) ||
            item.marketLabel.includes(f),
        ),
      }))
      .filter((g) => g.items.length > 0);
  }, [marketGroups, filter]);

  const handleCopyCode = useCallback((code: string, e: React.MouseEvent) => {
    e.stopPropagation();
    void navigator.clipboard.writeText(code).then(() => {
      setCopiedCode(code);
      setTimeout(() => setCopiedCode(null), 1500);
    });
  }, []);

  const handleCodeClick = useCallback(
    (code: string) => {
      onSelectStock?.(code);
    },
    [onSelectStock],
  );

  if (isLoading) {
    return (
      <div className={cn('rounded-xl border border-subtle bg-surface/70 px-4 py-3', className)}>
        <p className="text-xs text-muted-text">加载自选股列表...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className={cn('rounded-xl border border-subtle bg-surface/70 px-4 py-3', className)}>
        <p className="text-xs text-danger">{error}</p>
      </div>
    );
  }

  return (
    <div className={cn('rounded-xl border border-subtle bg-surface/70 shadow-sm', className)}>
      {/* Header */}
      <button
        type="button"
        onClick={() => setCollapsed(!collapsed)}
        className="flex w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-hover/50 rounded-t-xl"
      >
        {collapsed ? (
          <ChevronRight className="h-4 w-4 text-muted-text" />
        ) : (
          <ChevronDown className="h-4 w-4 text-muted-text" />
        )}
        <span className="text-sm font-semibold text-foreground">自选股列表</span>
        <span className="text-xs text-muted-text tabular-nums">({stocks.length} 只)</span>
        {/* Market count pills (visible when collapsed) */}
        {collapsed && (
          <div className="ml-auto flex flex-wrap gap-1">
            {marketGroups.map((g) => (
              <span
                key={g.key}
                className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium', g.color)}
              >
                {g.label} {g.items.length}
              </span>
            ))}
          </div>
        )}
      </button>

      {!collapsed && (
        <div className="px-4 pb-3 space-y-3">
          {/* Market stats row */}
          <div className="flex flex-wrap gap-1.5">
            {marketGroups.map((g) => (
              <span
                key={g.key}
                className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium', g.color)}
              >
                {g.label} {g.items.length}
              </span>
            ))}
          </div>

          {/* Search */}
          <div className="relative">
            <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-text" />
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="搜索股票代码..."
              className="w-full rounded-lg border border-subtle bg-surface/80 py-1.5 pl-7 pr-7 text-xs text-foreground placeholder:text-muted-text focus:border-primary/40 focus:outline-none focus:ring-1 focus:ring-primary/20"
            />
            {filter && (
              <button
                type="button"
                onClick={() => setFilter('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-text hover:text-foreground"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>

          {/* Stock list */}
          <div className="max-h-[300px] space-y-3 overflow-y-auto pr-1">
            {filteredGroups.length === 0 ? (
              <p className="text-xs text-muted-text py-2 text-center">无匹配结果</p>
            ) : (
              filteredGroups.map((group) => (
                <div key={group.key}>
                  <p className="text-[10px] font-medium text-muted-text mb-1.5 uppercase tracking-wider">
                    {group.label}
                  </p>
                  <div className="space-y-1">
                    {group.items.map((item) => (
                      <div
                        key={item.code}
                        className="group flex items-center gap-2 rounded-lg border border-subtle bg-surface px-2 py-1.5 transition-colors hover:border-primary/40 hover:bg-primary/5"
                      >
                        <button
                          type="button"
                          onClick={() => handleCodeClick(item.code)}
                          className="min-w-0 flex-1 truncate text-left font-mono text-xs text-foreground transition-colors hover:text-primary"
                          title={`点击搜索 ${item.code}`}
                        >
                          {item.code}
                        </button>
                        <span className={cn('shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium', group.color)}>
                          {item.marketLabel}
                        </span>
                        <button
                          type="button"
                          onClick={(e) => handleCopyCode(item.code, e)}
                          className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-text transition hover:bg-primary/10 hover:text-primary"
                          title="复制代码"
                        >
                          {copiedCode === item.code ? (
                            <span className="text-[10px] text-primary">✓</span>
                          ) : (
                            <Copy className="h-2.5 w-2.5" />
                          )}
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              ))
            )}
          </div>

          {/* Footer hint */}
          <div className="flex items-center justify-between">
            <p className="text-[10px] text-muted-text">
              点击代码可填入搜索框进行分析
            </p>
            <button
              type="button"
              onClick={() => navigate('/portfolio')}
              className="inline-flex items-center gap-1 text-[10px] text-cyan-600 transition hover:text-cyan-800"
            >
              <ExternalLink className="h-3 w-3" />
              管理自选股
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
