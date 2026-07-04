import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, BadgeDollarSign, Building2, Database, FileText, RefreshCw } from 'lucide-react';
import { Drawer } from '../common';
import { MARKET_LABELS } from '../../utils/market';
import { cn } from '../../utils/cn';
import { stocksApi, type StockMetaItem } from '../../api/stocks';

type DetailTab = 'profile' | 'valuation' | 'financial';

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
  { value: 'valuation', label: '估值', icon: BadgeDollarSign },
  { value: 'financial', label: '财务', icon: FileText },
];

function formatNumber(value: number | null, digits = 2) {
  return value == null ? null : value.toFixed(digits);
}

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
  const [tab, setTab] = useState<DetailTab>('profile');
  const [detail, setDetail] = useState<StockMetaItem | null>(stock);
  const [loadingSection, setLoadingSection] = useState<DetailTab | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [attempted, setAttempted] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setDetail(stock);
    setErrors({});
    setAttempted({});
    setLoadingSection(null);
    setTab('profile');
  }, [stock?.code]);

  const currentStock = detail || stock;

  const enrich = useCallback(async (section: 'valuation' | 'financial', force = false) => {
    if (!currentStock || loadingSection) return;
    const key = `${currentStock.code}:${section}`;
    if (!force && attempted[key]) return;

    setAttempted((prev) => ({ ...prev, [key]: true }));
    setLoadingSection(section);
    setErrors((prev) => {
      const next = { ...prev };
      delete next[section];
      return next;
    });

    try {
      const result = await stocksApi.enrichStock(currentStock.code, [section]);
      if (result.item) {
        setDetail(result.item);
      }
      if (result.errors?.[section]) {
        setErrors((prev) => ({ ...prev, [section]: result.errors[section] }));
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : '补齐失败';
      setErrors((prev) => ({ ...prev, [section]: message }));
    } finally {
      setLoadingSection(null);
    }
  }, [attempted, currentStock, loadingSection]);

  useEffect(() => {
    if (!open || !currentStock) return;
    const valuationMissing = (
      currentStock.pe_ttm == null
      || currentStock.pb == null
      || currentStock.total_market_cap == null
      || currentStock.circulating_market_cap == null
    );
    if (valuationMissing) {
      void enrich('valuation');
    }
  }, [currentStock, enrich, open]);

  useEffect(() => {
    if (!open || !currentStock || tab !== 'financial') return;
    if (!currentStock.financial_fetched_at) {
      void enrich('financial');
    }
  }, [currentStock, enrich, open, tab]);

  const groups = useMemo(() => {
    if (!currentStock) return null;
    const financialEmptyText = '按需获取';

    return {
      profile: [
        field('代码', currentStock.code),
        field('名称', currentStock.name),
        field('市场', MARKET_LABELS[currentStock.market] || currentStock.market),
        field('行业 / 板块', currentStock.sector),
        field('地区', currentStock.area),
        field('上市日期', currentStock.ipo_date),
        field('状态', currentStock.status === 'active' ? 'active · 正常' : currentStock.status),
        field('股票级同步时间', formatDateTime(currentStock.last_sync_at)),
      ],
      valuation: [
        field('总市值', formatMoney(currentStock.total_market_cap)),
        field('流通市值', formatMoney(currentStock.circulating_market_cap)),
        field('PE TTM', formatNumber(currentStock.pe_ttm, 2)),
        field('PB', formatNumber(currentStock.pb, 2)),
        field('今日成交额', formatMoney(currentStock.amount_today), financialEmptyText, '行情补充字段'),
      ],
      financial: [
        field('营收 TTM', formatMoney(currentStock.revenue_ttm), financialEmptyText, 'fundamental-filter 按需写入'),
        field('扣非净利润 TTM', formatMoney(currentStock.deducted_profit_ttm), financialEmptyText, 'fundamental-filter 按需写入'),
        field('经营现金流 TTM', formatMoney(currentStock.operating_cf_ttm), financialEmptyText, 'fundamental-filter 按需写入'),
        field('净利润 TTM', formatMoney(currentStock.net_profit_ttm), financialEmptyText, 'fundamental-filter 按需写入'),
        field('资产负债率', formatPercent(currentStock.debt_ratio), financialEmptyText),
        field('有息负债率', formatPercent(currentStock.interest_bearing_debt_ratio), financialEmptyText),
        field('现金债务比', formatPercent(currentStock.cash_debt_ratio), financialEmptyText),
        field('财报日期', currentStock.report_date, financialEmptyText),
        field('财务获取时间', formatDateTime(currentStock.financial_fetched_at), financialEmptyText),
      ],
    };
  }, [currentStock]);

  if (!currentStock || !groups) return null;

  const currentItems = groups[tab];
  const populated = currentItems.filter((item) => !item.empty).length;
  const refreshSection = tab === 'profile' ? null : tab;
  const refreshing = refreshSection ? loadingSection === refreshSection : false;

  return (
    <Drawer isOpen={open} onClose={onClose} title={`${currentStock.name} ${currentStock.code}`} width="max-w-xl">
      <div className="space-y-4">
        <div className="rounded-lg border border-border bg-muted/20 px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex min-w-0 items-center gap-2">
                <span className="truncate text-base font-semibold text-foreground">{currentStock.name}</span>
                <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                  {currentStock.code}
                </span>
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {MARKET_LABELS[currentStock.market] || currentStock.market}
                {currentStock.sector ? ` · ${currentStock.sector}` : ''}
                {currentStock.area ? ` · ${currentStock.area}` : ''}
              </div>
            </div>
            <button
              type="button"
              onClick={() => onViewKline({ code: currentStock.code, name: currentStock.name })}
              className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border bg-card text-muted-foreground transition hover:border-primary/40 hover:text-primary"
              title="查看K线"
            >
              <Activity className="h-4 w-4" />
            </button>
          </div>
          <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
            <div className="min-w-0 rounded-md bg-card px-2 py-2">
              <div className="text-[10px] text-muted-foreground">估值</div>
              <div className="mt-1 truncate font-mono font-semibold text-foreground">
                {currentStock.pe_ttm != null ? `PE ${currentStock.pe_ttm.toFixed(1)}` : 'PE -'}
              </div>
            </div>
            <div className="min-w-0 rounded-md bg-card px-2 py-2">
              <div className="text-[10px] text-muted-foreground">市值</div>
              <div className="mt-1 truncate font-mono font-semibold text-foreground">{formatMoney(currentStock.total_market_cap) || '-'}</div>
            </div>
            <div className="min-w-0 rounded-md bg-card px-2 py-2" title={formatDateTime(currentStock.last_sync_at) || undefined}>
              <div className="text-[10px] text-muted-foreground">同步</div>
              <div className="mt-1 truncate font-mono font-semibold text-foreground">{formatDateTime(currentStock.last_sync_at) || '-'}</div>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-1 rounded-lg border border-border bg-muted/30 p-1">
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

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Database className="h-3.5 w-3.5" />
            <span>{populated}/{currentItems.length} 个字段有值</span>
          </div>
          {refreshSection && (
            <button
              type="button"
              onClick={() => enrich(refreshSection, true)}
              disabled={refreshing}
              className="inline-flex h-7 items-center gap-1 rounded-md border border-border bg-card px-2 text-[10px] font-medium text-muted-foreground transition hover:border-primary/40 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
              title={refreshing ? '补齐中' : '刷新字段'}
            >
              <RefreshCw className={cn('h-3.5 w-3.5', refreshing && 'animate-spin')} />
              {refreshing ? '补齐中' : '刷新'}
            </button>
          )}
        </div>

        {errors[tab] && (
          <div className="rounded-md border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            {errors[tab]}
          </div>
        )}

        <FieldGrid items={currentItems} />
      </div>
    </Drawer>
  );
};
