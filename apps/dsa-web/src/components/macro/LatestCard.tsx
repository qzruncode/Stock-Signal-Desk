import { cn } from '../../utils/cn';
import { formatNumber, formatAmount, formatPct, pctColor } from '../../utils/macro';
import { StatBlock } from './StatBlock';
import type { IndexPoint } from '../../types/macro';

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