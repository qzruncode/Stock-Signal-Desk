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
 * 容器需有唯一 id:klinecharts 内部用 dom.id 做实例缓存 key,空 id 会导致多实例
 * 冲突。用 useId() 生成,init 传元素引用。
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
  const containerRef = useRef<HTMLDivElement | null>(null);
  // chartData 用 ref 持有:init 只做一次,其 setDataLoader 闭包需读最新数据,
  // 避免流式 recent 增量时 stale。ref 在 effect 里同步,不在渲染期写。
  const chartData = useMemo(() => toChartData(result?.recent), [result?.recent]);
  const chartDataRef = useRef(chartData);
  const latest = result?.latest;

  // 初始化 chart(仅一次):等容器有非 0 尺寸再 init,否则 klinecharts 测得 0 宽高
  // 不画柱子(白屏)。用 ResizeObserver 监听容器首次获得尺寸的时刻。
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    let disposed = false;

    const createChart = () => {
      if (disposed || chartRef.current) return;
      const chart = init(el);
      if (!chart) return;
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
          callback(chartDataRef.current, false);
        },
      });
      chart.setPeriod({ span: 1, type: 'day' });
    };

    // 容器已布局(有宽高)直接建图;否则等 ResizeObserver 报告首次非 0 尺寸
    if (el.clientWidth > 0 && el.clientHeight > 0) {
      createChart();
    }
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          if (!chartRef.current) {
            createChart();
          } else {
            chartRef.current.resize();
          }
        }
      }
    });
    ro.observe(el);

    return () => {
      disposed = true;
      ro.disconnect();
      if (chartRef.current) {
        try { dispose(el); } catch { /* ignore */ }
        chartRef.current = null;
      }
    };
  }, []);

  // 同步 chartData 到 ref,供 init 一次性注册的 loader 闭包读取最新数据
  useEffect(() => {
    chartDataRef.current = chartData;
  }, [chartData]);

  // 数据变化(流式 result.recent 增量到达 / symbol 变更):重新注册 loader 触发刷新。
  // chart 建图可能晚于首次数据(等容器有尺寸),此处 chartRef.current 为 null 时跳过,
  // 等 init 完成后由其 loader 读 chartDataRef 拿到数据;后续变化再由此 effect 刷新。
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.setSymbol({ ticker: args.symbol ?? '' });
    chart.setDataLoader({
      getBars: ({ callback }) => {
        callback(chartData, false);
      },
    });
    chart.resize();
  }, [chartData, args.symbol]);

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
      <div id={containerId} ref={containerRef} className="h-[220px] w-full" />
    </div>
  );
};

export default KlineToolUI;
