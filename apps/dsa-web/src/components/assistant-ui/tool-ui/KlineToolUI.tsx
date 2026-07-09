import { useEffect, useId, useMemo, useRef } from 'react';
import { init, dispose } from 'klinecharts';
import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import { cn } from '../../../utils/cn';
import { formatPct, type KlineToolResult } from '../../../utils/toolResults';
import { ToolStatusPill } from './shared';

const COLORS = {
  up: '#EF4444',
  down: '#22C55E',
  noChange: '#94A3B8',
  grid: '#E2E8F0',
};

function toChartData(recent: KlineToolResult['recent']) {
  return (recent ?? [])
    .filter((bar) => bar.open != null && bar.close != null && bar.high != null && bar.low != null)
    .map((bar) => ({
      timestamp: new Date(bar.date + 'T00:00:00+08:00').getTime(),
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
      volume: bar.volume ?? undefined,
      turnover: bar.turnover_rate ?? undefined,
    }));
}

/**
 * 内联迷你 K 线图工具 UI。
 * 每实例用 useId() 生成容器 id,避免 KLineChartPanel 单例 DOM id 冲突。
 */
const KlineToolUI = ({
  args,
  result,
  status,
  isError,
}: ToolCallMessagePartProps<{ symbol: string; count?: number }, KlineToolResult>) => {
  const reactId = useId();
  const containerId = `kline-tool-${reactId.replace(/[^a-zA-Z0-9]/g, '')}`;
  const chartRef = useRef<ReturnType<typeof init> | null>(null);
  const chartData = useMemo(() => toChartData(result?.recent), [result?.recent]);
  const latest = result?.latest;

  useEffect(() => {
    if (chartData.length === 0) return undefined;
    const chart = init(containerId);
    if (!chart) return undefined;
    chartRef.current = chart;
    chart.setStyles({
      grid: {
        show: true,
        horizontal: { show: true, size: 1, color: COLORS.grid, style: 'solid', dashedValue: [2, 2] },
        vertical: { show: true, size: 1, color: COLORS.grid, style: 'solid', dashedValue: [2, 2] },
      },
      candle: {
        type: 'candle_solid',
        bar: {
          upColor: COLORS.up,
          downColor: COLORS.down,
          noChangeColor: COLORS.noChange,
          upBorderColor: COLORS.up,
          downBorderColor: COLORS.down,
          noChangeBorderColor: COLORS.noChange,
          upWickColor: COLORS.up,
          downWickColor: COLORS.down,
          noChangeWickColor: COLORS.noChange,
        },
      },
      xAxis: { show: true, axisLine: { show: true, color: COLORS.grid }, tickLine: { show: false }, tickText: { show: true, color: '#76808F', size: 10 } },
      yAxis: { show: true, axisLine: { show: true, color: COLORS.grid }, tickLine: { show: false }, tickText: { show: true, color: '#76808F', size: 10 } },
      separator: { size: 1, color: COLORS.grid, fill: true, activeBackgroundColor: 'rgba(0,0,0,0)' },
    });
    chart.createIndicator('VOL', { isStack: false });
    chart.setDataLoader({
      getBars: ({ callback }) => {
        callback(chartData, false);
      },
    });
    if (args.symbol) {
      chart.setSymbol({ ticker: args.symbol });
    }
    chart.setPeriod({ span: 1, type: 'day' });
    return () => {
      try { dispose(containerId); } catch { /* ignore */ }
      chartRef.current = null;
    };
  }, [containerId, chartData, args.symbol]);

  const symbolLabel = args.symbol?.trim();

  if (status.type === 'running' && !result) {
    return <ToolStatusPill status={status} isError={isError} label={symbolLabel ? `正在获取 ${symbolLabel} K线数据…` : '正在获取K线数据…'} />;
  }
  if ((isError || (status.type === 'incomplete' && status.reason === 'error')) && !result) {
    return <ToolStatusPill status={status} isError label="获取K线数据失败" />;
  }
  if (!result || chartData.length === 0) {
    return <ToolStatusPill status={status} isError label="无K线数据" />;
  }

  const pct = latest?.pct_chg;

  return (
    <div className="my-2 overflow-hidden rounded-xl border border-border bg-card/60">
      <div className="flex items-center justify-between gap-3 border-b border-border px-3 py-2">
        <span className="text-xs font-medium text-foreground">
          {symbolLabel ?? 'K线'}
          <span className="ml-2 text-muted-foreground">近 {chartData.length} 日</span>
        </span>
        {pct != null && (
          <span className={cn('text-xs font-semibold', pct > 0 ? 'text-red-500' : pct < 0 ? 'text-emerald-500' : 'text-muted-foreground')}>
            {formatPct(pct)}
          </span>
        )}
      </div>
      <div id={containerId} className="h-[220px] w-full" />
    </div>
  );
};

export default KlineToolUI;
