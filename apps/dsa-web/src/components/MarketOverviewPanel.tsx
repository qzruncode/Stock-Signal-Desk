import React from 'react';
import {
  BarChart3,
  Clock,
  Landmark,
  TrendingUp,
} from 'lucide-react';
import type { MarketStatus } from '../api/market';
import { cn } from '../utils/cn';

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function formatAmount(value: number | null | undefined): string {
  if (value == null) return '-';
  if (value >= 1e4) return `${(value / 1e4).toFixed(2)}万亿`;
  if (value >= 1e2) return `${value.toFixed(0)}亿`;
  return `${value.toFixed(2)}亿`;
}

function formatFlow(value: number | null | undefined): string {
  if (value == null) return '-';
  const abs = Math.abs(value);
  const sign = value >= 0 ? '+' : '-';
  if (abs >= 1e2) return `${sign}${abs.toFixed(0)}亿`;
  return `${sign}${abs.toFixed(2)}亿`;
}

// ---------------------------------------------------------------------------
// Stat item
// ---------------------------------------------------------------------------

function StatItem({
  label,
  value,
  highlight,
  highlightUp,
  highlightDown,
}: {
  label: string;
  value: string;
  highlight?: boolean;
  highlightUp?: boolean;
  highlightDown?: boolean;
}) {
  const valueColor = highlightUp
    ? 'text-red-600 font-semibold'
    : highlightDown
      ? 'text-green-600 font-semibold'
      : highlight
        ? 'text-slate-900 font-semibold'
        : 'text-slate-700';
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-slate-400">{label}</span>
      <span className={cn('tabular-nums text-sm', valueColor)}>{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Mini bar chart for up/down ratio
// ---------------------------------------------------------------------------

function BreadthBar({
  up,
  down,
  flat,
}: {
  up: number;
  down: number;
  flat: number;
}) {
  const total = up + down + flat || 1;
  const upPct = (up / total) * 100;
  const downPct = (down / total) * 100;
  const flatPct = (flat / total) * 100;

  return (
    <div className="mt-2 flex h-2 w-full overflow-hidden rounded-full bg-slate-100">
      {upPct > 0 && (
        <div
          className="bg-red-400 transition-all duration-300"
          style={{ width: `${upPct}%` }}
        />
      )}
      {flatPct > 0 && (
        <div
          className="bg-slate-300 transition-all duration-300"
          style={{ width: `${flatPct}%` }}
        />
      )}
      {downPct > 0 && (
        <div
          className="bg-green-400 transition-all duration-300"
          style={{ width: `${downPct}%` }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

interface Props {
  data: MarketStatus | null;
  loading: boolean;
  error: string | null;
}

const MarketOverviewPanel: React.FC<Props> = ({ data, loading, error }) => {
  // --- Loading ---
  if (loading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
          <span className="text-sm text-slate-400">正在获取市场数据...</span>
        </div>
      </div>
    );
  }

  // --- Error ---
  if (error && !data) {
    return (
      <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 p-8 text-center">
        <p className="text-sm font-medium text-red-600">{error}</p>
      </div>
    );
  }

  // --- Empty ---
  if (!data) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
        <p className="text-sm text-slate-400">暂无市场数据</p>
      </div>
    );
  }

  // --- Data ---
  const shIdx = data.sh_index;
  const isTrading = data.is_trading_time;
  const total = data.up_count + data.down_count + data.flat_count || 1;

  return (
    <div className="space-y-4">
      {/* Header card: 上证指数 */}
      {shIdx && (
        <div className={cn(
          'rounded-2xl border p-6 shadow-sm',
          isTrading ? 'bg-white' : 'bg-slate-50/70',
        )}>
          <div className="flex items-center justify-between">
            <div>
              <div className="flex items-center gap-2">
                <Landmark className="h-4 w-4 text-amber-500" />
                <span className="text-sm font-medium text-slate-500">上证指数</span>
              </div>
              <div className="mt-1 flex items-baseline gap-3">
                <span className="text-3xl font-bold tabular-nums text-slate-900">
                  {shIdx.close != null ? shIdx.close.toFixed(2) : '-'}
                </span>
                {shIdx.open != null && (
                  <span className="text-sm text-slate-400">
                    开 {shIdx.open.toFixed(2)}
                  </span>
                )}
              </div>
              <div className="mt-1 flex items-center gap-3 text-xs text-slate-400">
                {shIdx.high != null && <span>高 {shIdx.high.toFixed(2)}</span>}
                {shIdx.low != null && <span>低 {shIdx.low.toFixed(2)}</span>}
              </div>
            </div>

            {/* Trading status badge */}
            <div className={cn(
              'flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium',
              isTrading
                ? 'bg-red-50 text-red-600'
                : 'bg-slate-100 text-slate-400',
            )}>
              <span className={cn(
                'h-1.5 w-1.5 rounded-full',
                isTrading ? 'bg-red-500 animate-pulse' : 'bg-slate-300',
              )} />
              {isTrading ? '交易中' : '已收盘'}
            </div>
          </div>
        </div>
      )}

      {/* Market breadth: 涨跌统计 */}
      <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
          <BarChart3 className="h-4 w-4 text-cyan-600" />涨跌统计
        </h3>

        <div className="grid grid-cols-3 gap-x-4 gap-y-2 sm:grid-cols-3">
          <StatItem label="上涨家数" value={data.up_count.toLocaleString()} highlightUp />
          <StatItem label="下跌家数" value={data.down_count.toLocaleString()} highlightDown />
          <StatItem label="平盘家数" value={data.flat_count.toLocaleString()} />
        </div>

        <BreadthBar up={data.up_count} down={data.down_count} flat={data.flat_count} />

        <div className="mt-1 flex justify-between text-xs text-slate-400">
          <span className="text-red-400">{((data.up_count / total) * 100).toFixed(1)}%</span>
          <span className="text-slate-400">{((data.flat_count / total) * 100).toFixed(1)}%</span>
          <span className="text-green-400">{((data.down_count / total) * 100).toFixed(1)}%</span>
        </div>
      </div>

      {/* Limit up/down + Totals */}
      <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
          <TrendingUp className="h-4 w-4 text-cyan-600" />成交与情绪
        </h3>

        <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
          <StatItem label="涨停家数" value={data.limit_up_count.toLocaleString()} highlightUp />
          <StatItem label="跌停家数" value={data.limit_down_count.toLocaleString()} highlightDown />
          <StatItem label="两市成交额" value={formatAmount(data.total_amount)} highlight />
          <StatItem
            label="北向资金"
            value={formatFlow(data.north_flow)}
            highlightUp={data.north_flow > 0}
            highlightDown={data.north_flow < 0}
          />
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center gap-1.5 text-xs text-slate-400">
        <Clock className="h-3 w-3" />
        <span>
          数据获取时间: {data._fetched_at ? new Date(data._fetched_at).toLocaleString('zh-CN') : '-'}
          {data._cached ? ' · 缓存' : ' · 实时'}
        </span>
      </div>
    </div>
  );
};

export default MarketOverviewPanel;
