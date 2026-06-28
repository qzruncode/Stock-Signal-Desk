import React from 'react';
import type { BatchRunItem } from '../../api/batch';

interface StatsCardsProps {
  run: BatchRunItem;
  successCount: number;
  failedCount: number;
  successRate: string;
  passedCount: number;
}

export const StatsCards: React.FC<StatsCardsProps> = ({ run, successCount, failedCount, successRate, passedCount }) => (
  <section className="grid gap-3 sm:grid-cols-5">
    <div className="rounded-lg border border-subtle bg-surface px-4 py-3">
      <p className="text-[10px] uppercase tracking-wider text-muted-text">股票数</p>
      <p className="mt-1 text-xl font-semibold text-foreground">{run.stock_count}</p>
    </div>
    <div className="rounded-lg border border-subtle bg-surface px-4 py-3">
      <p className="text-[10px] uppercase tracking-wider text-muted-text">成功</p>
      <p className="mt-1 text-xl font-semibold text-emerald-600">{successCount}</p>
    </div>
    <div className="rounded-lg border border-subtle bg-surface px-4 py-3">
      <p className="text-[10px] uppercase tracking-wider text-muted-text">失败</p>
      <p className="mt-1 text-xl font-semibold text-red-600">{failedCount}</p>
    </div>
    <div className="rounded-lg border border-subtle bg-surface px-4 py-3">
      <p className="text-[10px] uppercase tracking-wider text-muted-text">成功率</p>
      <p className="mt-1 text-xl font-semibold text-foreground">{successRate}%</p>
    </div>
    <div className="rounded-lg border border-subtle bg-surface px-4 py-3">
      <p className="text-[10px] uppercase tracking-wider text-muted-text">筛选通过</p>
      <p className="mt-1 text-xl font-semibold text-cyan-700">{passedCount}</p>
    </div>
  </section>
);