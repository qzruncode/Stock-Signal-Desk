import { ArrowDown, ArrowUp, Clock, Minus } from 'lucide-react';
import { cn } from '../utils/cn';
import { formatNumber, formatAmount, formatPct, pctColor } from '../utils/macro';
import type { IndexPoint } from '../types/macro';

// ---- ArrowIcon / TrendIcon / StatBlock / PageFooter / LatestCard / HistoryTable ----

export function ArrowIcon({ value }: { value: number | null | undefined }) {
  if (value == null) return <Minus className="h-4 w-4 text-slate-400" />;
  return value >= 0 ? (
    <ArrowUp className="h-4 w-4 text-red-600" />
  ) : (
    <ArrowDown className="h-4 w-4 text-green-600" />
  );
}

export function TrendIcon({ trend }: { trend: string }) {
  if (trend === '上升') return <ArrowUp className="h-4 w-4 text-red-600" />;
  if (trend === '下降') return <ArrowDown className="h-4 w-4 text-green-600" />;
  return <Minus className="h-4 w-4 text-slate-400" />;
}

export function StatBlock({ label, value, valueClassName }: { label: string; value: string; valueClassName?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-slate-400">{label}</span>
      <span className={cn('tabular-nums text-sm text-slate-700', valueClassName)}>{value}</span>
    </div>
  );
}

export function PageFooter({ data }: { data: { _fetched_at: string; _cached: boolean; source: string } }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
      <Clock className="h-3 w-3" />
      <span>
        数据获取时间: {data._fetched_at ? new Date(data._fetched_at).toLocaleString('zh-CN') : '-'}
        {data._cached ? ' · 缓存' : ' · 实时'}
      </span>
      <span className="text-slate-300">|</span>
      <span>数据源: {data.source}</span>
    </div>
  );
}

export function LatestCard({ latest }: { latest: IndexPoint }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-900">最新行情</h3>
        {latest.date && <span className="text-xs text-slate-400">{latest.date}</span>}
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
        <StatBlock label="收盘" value={formatNumber(latest.close)} />
        <StatBlock label="开盘" value={formatNumber(latest.open)} />
        <StatBlock label="最高" value={formatNumber(latest.high)} />
        <StatBlock label="最低" value={formatNumber(latest.low)} />
        <StatBlock
          label="涨跌幅"
          value={formatPct(latest.pct_chg)}
          valueClassName={cn('tabular-nums text-sm font-medium', pctColor(latest.pct_chg))}
        />
        {latest.change_amount != null && (
          <StatBlock
            label="涨跌额"
            value={formatNumber(latest.change_amount)}
            valueClassName={cn('tabular-nums text-sm font-medium', pctColor(latest.change_amount))}
          />
        )}
        <StatBlock label="成交量" value={formatAmount(latest.volume)} />
        <StatBlock label="成交额" value={formatAmount(latest.amount)} />
      </div>
    </div>
  );
}

export function HistoryTable({ history }: { history: IndexPoint[] }) {
  if (history.length === 0) return null;

  const rows = [...history].reverse();

  return (
    <div className="rounded-2xl border border-slate-200 bg-white">
      <div className="px-4 py-3">
        <h3 className="text-sm font-semibold text-slate-900">历史数据</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-t border-slate-100 text-xs text-slate-400">
              <th className="px-4 py-2 text-left font-medium">日期</th>
              <th className="px-4 py-2 text-right font-medium">收盘</th>
              <th className="px-4 py-2 text-right font-medium">开盘</th>
              <th className="px-4 py-2 text-right font-medium">最高</th>
              <th className="px-4 py-2 text-right font-medium">最低</th>
              <th className="px-4 py-2 text-right font-medium">涨跌幅</th>
              <th className="px-4 py-2 text-right font-medium">成交量</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.date} className={cn('border-t border-slate-50', i % 2 === 0 ? 'bg-slate-50/50' : 'bg-white')}>
                <td className="px-4 py-2 text-slate-600">{row.date}</td>
                <td className="px-4 py-2 text-right tabular-nums text-slate-800">{formatNumber(row.close)}</td>
                <td className="px-4 py-2 text-right tabular-nums text-slate-600">{formatNumber(row.open)}</td>
                <td className="px-4 py-2 text-right tabular-nums text-slate-600">{formatNumber(row.high)}</td>
                <td className="px-4 py-2 text-right tabular-nums text-slate-600">{formatNumber(row.low)}</td>
                <td className={cn('px-4 py-2 text-right tabular-nums text-sm font-medium', pctColor(row.pct_chg))}>
                  <ArrowIcon value={row.pct_chg} />
                  <span className="ml-1">{formatPct(row.pct_chg)}</span>
                </td>
                <td className="px-4 py-2 text-right tabular-nums text-slate-500">{formatAmount(row.volume)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
