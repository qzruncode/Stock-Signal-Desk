import { cn } from '../../utils/cn';
import { formatNumber, formatAmount, formatPct, pctColor } from '../../utils/macro';
import { ArrowIcon } from './icons';
import type { IndexPoint } from '../../types/macro';

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