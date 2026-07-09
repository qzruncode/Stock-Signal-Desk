import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import { cn } from '../../../utils/cn';
import { formatNum, formatPct, type FinancialsToolResult, type FinancialPeriod } from '../../../utils/toolResults';
import { ToolStatusPill } from './shared';

const PERIODS: Array<{ key: keyof FinancialPeriod; label: string; pct?: boolean }> = [
  { key: 'report_date', label: '报告期' },
  { key: 'revenue', label: '营收' },
  { key: 'revenue_yoy', label: '营收同比', pct: true },
  { key: 'net_profit', label: '净利' },
  { key: 'net_profit_yoy', label: '净利同比', pct: true },
  { key: 'eps', label: 'EPS' },
  { key: 'roe', label: 'ROE(%)' },
  { key: 'gross_margin', label: '毛利率(%)' },
];

function fmt(value: unknown, pct?: boolean): string {
  if (value == null || value === '') return '-';
  const num = typeof value === 'number' ? value : Number(value);
  if (Number.isNaN(num)) return String(value);
  return pct ? formatPct(num) : formatNum(num);
}

function toneForYoY(value: unknown): string {
  if (value == null) return '';
  const num = typeof value === 'number' ? value : Number(value);
  if (Number.isNaN(num) || num === 0) return '';
  return num > 0 ? 'text-red-500' : 'text-emerald-500';
}

/**
 * 财务摘要内联表格。展示最近若干报告期的关键指标。
 */
const FinancialsToolUI = ({
  args,
  result,
  status,
  isError,
}: ToolCallMessagePartProps<{ symbol: string; periods?: number }, FinancialsToolResult>) => {
  if (status.type === 'running' && !result) {
    return <ToolStatusPill status={status} isError={isError} streamingFields={['symbol']} label={`正在获取 ${args.symbol} 财务数据…`} />;
  }
  if ((isError || (status.type === 'incomplete' && status.reason === 'error')) && !result) {
    return <ToolStatusPill status={status} isError label="获取财务数据失败" />;
  }

  const periods = result?.recent_periods ?? (result?.latest ? [result.latest] : []);
  if (!result || periods.length === 0) {
    return <ToolStatusPill status={status} isError label="无财务数据" />;
  }

  return (
    <div className="my-3 w-full min-w-0 overflow-hidden rounded-xl border border-border bg-card/60">
      <div className="border-b border-border px-3 py-2 text-xs font-medium text-foreground">
        {args.symbol} · 财务摘要
        <span className="ml-2 text-muted-foreground">近 {periods.length} 期</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-max min-w-full border-collapse text-left text-[11px]">
          <thead>
            <tr>
              {PERIODS.map((p) => (
                <th key={String(p.key)} className="border-b border-border bg-muted px-3 py-2 font-semibold whitespace-nowrap text-foreground">
                  {p.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {periods.map((period, idx) => (
              <tr key={(period.report_date ?? '') + idx} className="[&:last-child_td]:border-b-0">
                {PERIODS.map((p) => {
                  const value = period[p.key];
                  const isYoY = p.key === 'revenue_yoy' || p.key === 'net_profit_yoy';
                  return (
                    <td
                      key={String(p.key)}
                      className={cn(
                        'border-b border-border px-3 py-2 align-top leading-6 tabular-nums',
                        isYoY ? toneForYoY(value) : 'text-foreground/85',
                      )}
                    >
                      {fmt(value, p.pct)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default FinancialsToolUI;
