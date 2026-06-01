import React from 'react';
import { cn } from '../utils/cn';
import type { MarketBreadthResponse } from '../types/macro';
import { formatAmount } from '../utils/macro';
import { PageFooter } from './macro';

interface MarketBreadthSectionProps {
  data: MarketBreadthResponse | null;
  loading: boolean;
  error: string | null;
}

const MarketBreadthSection: React.FC<MarketBreadthSectionProps> = ({ data, loading, error }) => {
  if (loading && !data) {
    return (
      <div className="flex h-20 items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 p-4 text-center">
        <p className="text-sm font-medium text-red-600">{error}</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-4 text-center">
        <p className="text-sm text-slate-400">暂无市场宽度数据</p>
      </div>
    );
  }

  const breadthRatio = data.advance_decline_ratio;

  return (
    <div className="space-y-3">
      {/* Top stats — 涨跌概况 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          label="上涨家数"
          value={data.up_count != null ? data.up_count.toLocaleString() : '-'}
          valueClassName="text-red-600"
        />
        <StatCard
          label="下跌家数"
          value={data.down_count != null ? data.down_count.toLocaleString() : '-'}
          valueClassName="text-green-600"
        />
        <StatCard
          label="涨跌比"
          value={breadthRatio != null ? breadthRatio.toFixed(2) : '-'}
          valueClassName={breadthRatio != null && breadthRatio >= 1 ? 'text-red-600' : 'text-green-600'}
        />
        <StatCard
          label="两市成交额"
          value={data.volume != null ? formatAmount(data.volume) : '-'}
        />
      </div>

      {/* Secondary stats — 涨停/跌停 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          label="涨停家数"
          value={data.limit_up_count != null ? String(data.limit_up_count) : '-'}
          valueClassName="text-red-600"
        />
        <StatCard
          label="跌停家数"
          value={data.limit_down_count != null ? String(data.limit_down_count) : '-'}
          valueClassName="text-green-600"
        />
        <StatCard
          label="炸板率"
          value={data.broken_board_rate != null ? `${data.broken_board_rate}%` : '-'}
          valueClassName={data.broken_board_rate != null && data.broken_board_rate > 30 ? 'text-red-600' : 'text-green-600'}
        />
        <StatCard
          label="平盘家数"
          value={data.flat_count != null ? data.flat_count.toLocaleString() : '-'}
        />
      </div>

      {/* Tertiary stats — 新高新低 + 连涨连跌 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          label="创60日新高"
          value={data.new_high_60d != null ? String(data.new_high_60d) : '-'}
          valueClassName="text-red-600"
        />
        <StatCard
          label="创60日新低"
          value={data.new_low_60d != null ? String(data.new_low_60d) : '-'}
          valueClassName="text-green-600"
        />
        <StatCard
          label="连涨天数"
          value={data.consecutive_up_days != null ? `${data.consecutive_up_days}天↑` : '-'}
        />
        <StatCard
          label="连跌天数"
          value={data.consecutive_down_days != null ? `${data.consecutive_down_days}天↓` : '-'}
        />
      </div>

      <PageFooter data={data} />
    </div>
  );
};

function StatCard({
  label,
  value,
  valueClassName,
}: {
  label: string;
  value: string;
  valueClassName?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3">
      <span className="text-xs text-slate-400">{label}</span>
      <p className={cn('mt-1 text-lg font-semibold tabular-nums', valueClassName ?? 'text-slate-800')}>
        {value}
      </p>
    </div>
  );
}

export default MarketBreadthSection;
