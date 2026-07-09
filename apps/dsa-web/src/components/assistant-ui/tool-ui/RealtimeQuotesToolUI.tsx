import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import { cn } from '../../../utils/cn';
import { formatNum, formatPct, formatAmount, type RealtimeQuotesToolResult } from '../../../utils/toolResults';
import { ToolStatusPill } from './shared';

/**
 * 实时行情内联卡片。A 股习惯:红涨绿跌。
 */
const RealtimeQuotesToolUI = ({
  args,
  result,
  status,
  isError,
}: ToolCallMessagePartProps<{ symbols: string }, RealtimeQuotesToolResult>) => {
  const symbolsLabel = args.symbols?.trim();
  if (status.type === 'running' && !result) {
    return <ToolStatusPill status={status} isError={isError} label={symbolsLabel ? `正在查询 ${symbolsLabel} 实时行情…` : '正在查询实时行情…'} />;
  }
  if ((isError || (status.type === 'incomplete' && status.reason === 'error')) && !result) {
    return <ToolStatusPill status={status} isError label="查询实时行情失败" />;
  }
  if (!result || !result.items?.length) {
    return <ToolStatusPill status={status} isError label="无实时行情数据" />;
  }

  return (
    <div className="my-2 space-y-2">
      {result.items.map((item, idx) => {
        const up = (item.pct_chg ?? 0) > 0;
        const flat = (item.pct_chg ?? 0) === 0;
        const tone = flat ? 'text-muted-foreground' : up ? 'text-red-500' : 'text-emerald-500';
        // symbol 经后端 compact 映射后应存在;兜底用索引避免 key 为 undefined/重复
        const key = item.symbol ?? `quote-${idx}`;
        return (
          <div key={key} className="rounded-xl border border-border bg-card/60 p-3">
            <div className="flex items-baseline justify-between gap-2">
              <div className="min-w-0">
                <span className="text-sm font-semibold text-foreground">{item.name}</span>
                {item.symbol && <span className="ml-2 font-mono text-xs text-muted-foreground">{item.symbol}</span>}
              </div>
              <div className="flex items-baseline gap-2">
                <span className={cn('text-lg font-bold tabular-nums', tone)}>{formatNum(item.price)}</span>
                <span className={cn('text-xs font-medium tabular-nums', tone)}>
                  {formatPct(item.pct_chg)} · {formatNum(item.change)}
                </span>
              </div>
            </div>
            <div className="mt-2 grid grid-cols-3 gap-x-3 gap-y-1 text-[11px] text-muted-foreground sm:grid-cols-4">
              <Field label="开" value={formatNum(item.open)} tone={tone} />
              <Field label="高" value={formatNum(item.high)} tone={tone} />
              <Field label="低" value={formatNum(item.low)} tone={tone} />
              <Field label="量" value={formatAmount(item.volume)} />
              <Field label="额" value={formatAmount(item.amount)} />
              {item.pe != null && <Field label="PE" value={formatNum(item.pe)} />}
              {item.pb != null && <Field label="PB" value={formatNum(item.pb)} />}
              {item.total_mv != null && <Field label="总市值" value={formatAmount(item.total_mv)} />}
            </div>
          </div>
        );
      })}
      {result.total > result.items.length && (
        <p className="text-[11px] text-muted-foreground">共 {result.total} 条,已展示前 {result.items.length} 条</p>
      )}
    </div>
  );
};

const Field: React.FC<{ label: string; value: string; tone?: string }> = ({ label, value, tone }) => (
  <div className="flex items-center justify-between gap-1">
    <span>{label}</span>
    <span className={cn('font-medium tabular-nums text-foreground', tone)}>{value}</span>
  </div>
);

export default RealtimeQuotesToolUI;
