import React, { useMemo, useState } from 'react';
import { Activity, Building2, Database, FileText } from 'lucide-react';
import { Drawer } from '../common';
import { MARKET_LABELS } from '../../utils/market';
import { cn } from '../../utils/cn';
import { type StockMetaItem } from '../../api/stocks';

type DetailTab = 'profile' | 'financial';

interface StockDetailDrawerProps {
  stock: StockMetaItem | null;
  open: boolean;
  onClose: () => void;
  onViewKline: (stock: { code: string; name: string }) => void;
}

interface FieldItem {
  label: string;
  value: React.ReactNode;
  empty?: boolean;
  hint?: string;
}

const tabs: Array<{ value: DetailTab; label: string; icon: React.ComponentType<{ className?: string }> }> = [
  { value: 'profile', label: '基础', icon: Building2 },
  { value: 'financial', label: '财务', icon: FileText },
];

function formatMoney(value: number | null) {
  if (value == null) return null;
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`;
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(2)} 万`;
  return value.toFixed(0);
}

function formatPercent(value: number | null) {
  return value == null ? null : `${value.toFixed(2)}%`;
}

function formatDateTime(value: string | null) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function field(label: string, value: React.ReactNode | null | undefined, emptyText = '未入库', hint?: string): FieldItem {
  const isEmpty = value == null || value === '';
  return { label, value: isEmpty ? emptyText : value, empty: isEmpty, hint };
}

const FieldGrid: React.FC<{ items: FieldItem[] }> = ({ items }) => (
  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
    {items.map((item) => (
      <div key={item.label} className="min-w-0 rounded-md border border-border bg-muted/30 px-3 py-2">
        <div className="text-[10px] font-medium text-muted-foreground">{item.label}</div>
        <div
          className={cn(
            'mt-1 min-w-0 break-words font-mono text-xs font-semibold',
            item.empty ? 'text-muted-foreground/55' : 'text-foreground',
          )}
        >
          {item.value}
        </div>
        {item.hint && <div className="mt-1 text-[10px] text-muted-foreground/70">{item.hint}</div>}
      </div>
    ))}
  </div>
);

export const StockDetailDrawer: React.FC<StockDetailDrawerProps> = ({ stock, open, onClose, onViewKline }) => {
  // tab 在父组件用 key={stock?.code} 触发 remount 来重置（见 MarketStocksPage）
  const [tab, setTab] = useState<DetailTab>('profile');

  const groups = useMemo(() => {
    if (!stock) return null;
    const financialEmptyText = '未同步';

    return {
      profile: [
        field('代码', stock.code),
        field('名称', stock.name),
        field('市场', MARKET_LABELS[stock.market] || stock.market),
        field('行业 / 板块', stock.sector),
        field('上市日期', stock.ipo_date),
        field('状态', stock.status === 'active' ? 'active · 正常' : stock.status),
        field('股票级同步时间', formatDateTime(stock.last_sync_at)),
      ],
      financial: [
        field('营收（最新报告期累计）', formatMoney(stock.revenue_latest), financialEmptyText, '业绩快报批量写入'),
        field('净利润（最新报告期累计）', formatMoney(stock.net_profit_latest), financialEmptyText, '业绩快报批量写入'),
        field('经营现金流（最新报告期累计）', formatMoney(stock.operating_cf_latest), financialEmptyText, '业绩快报批量写入'),
        field('资产负债率', formatPercent(stock.debt_ratio), financialEmptyText, '业绩快报批量写入'),
        field('报告期', stock.report_date, financialEmptyText),
        field('财务获取时间', formatDateTime(stock.financial_fetched_at), financialEmptyText),
      ],
    };
  }, [stock]);

  if (!stock || !groups) return null;

  const currentItems = groups[tab];
  const populated = currentItems.filter((item) => !item.empty).length;

  return (
    <Drawer isOpen={open} onClose={onClose} title={`${stock.name} ${stock.code}`} width="max-w-xl">
      <div className="space-y-4">
        <div className="rounded-lg border border-border bg-muted/20 px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex min-w-0 items-center gap-2">
                <span className="truncate text-base font-semibold text-foreground">{stock.name}</span>
                <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                  {stock.code}
                </span>
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {MARKET_LABELS[stock.market] || stock.market}
                {stock.sector ? ` · ${stock.sector}` : ''}
              </div>
            </div>
            <button
              type="button"
              onClick={() => onViewKline({ code: stock.code, name: stock.name })}
              className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border bg-card text-muted-foreground transition hover:border-primary/40 hover:text-primary"
              title="查看K线"
            >
              <Activity className="h-4 w-4" />
            </button>
          </div>
          <div className="mt-3 grid grid-cols-1 gap-2 text-xs">
            <div className="min-w-0 rounded-md bg-card px-2 py-2" title={formatDateTime(stock.last_sync_at) || undefined}>
              <div className="text-[10px] text-muted-foreground">同步</div>
              <div className="mt-1 truncate font-mono font-semibold text-foreground">{formatDateTime(stock.last_sync_at) || '-'}</div>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-1 rounded-lg border border-border bg-muted/30 p-1">
          {tabs.map((item) => {
            const Icon = item.icon;
            const active = tab === item.value;
            return (
              <button
                key={item.value}
                type="button"
                onClick={() => setTab(item.value)}
                className={cn(
                  'inline-flex h-8 items-center justify-center gap-1.5 rounded-md text-xs font-semibold transition',
                  active ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                <Icon className="h-3.5 w-3.5" />
                {item.label}
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Database className="h-3.5 w-3.5" />
          <span>{populated}/{currentItems.length} 个字段有值</span>
        </div>

        <FieldGrid items={currentItems} />
      </div>
    </Drawer>
  );
};
