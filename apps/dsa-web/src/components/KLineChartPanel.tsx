import React, { useEffect, useMemo, useRef } from 'react';
import { init, dispose } from 'klinecharts';
import type { KlineBar, KlineResponse } from '../api/kline';
import { cn } from '../utils/cn';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CHART_CONTAINER_ID = 'kline-chart-container';

const COLORS = {
  up: '#EF4444',
  down: '#22C55E',
  noChange: '#94A3B8',
  grid: '#E2E8F0',
};

const SOURCE_LABELS: Record<string, string> = {
  eastmoney: '东方财富',
  sina: '新浪财经',
  tencent: '腾讯财经',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function toChartData(bars: KlineBar[]) {
  return bars
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

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface KLineChartPanelProps {
  data: KlineResponse | null;
  loading: boolean;
  error: string | null;
  className?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const KLineChartPanel: React.FC<KLineChartPanelProps> = ({
  data,
  loading,
  error,
  className,
}) => {
  const chartRef = useRef<ReturnType<typeof init> | null>(null);
  const chartData = useMemo(() => toChartData(data?.data ?? []), [data?.data]);
  const hasData = chartData.length > 0;
  const symbol = data?.symbol;

  // Initialize chart when data becomes available
  useEffect(() => {
    if (!hasData) return;

    const timer = setTimeout(() => {
      const el = document.getElementById(CHART_CONTAINER_ID);
      if (!el) return;

      if (chartRef.current) {
        try { dispose(CHART_CONTAINER_ID); } catch { /* ignore */ }
        chartRef.current = null;
      }

      const chart = init(CHART_CONTAINER_ID);
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
            compareRule: 'current_open',
            upColor: COLORS.up, downColor: COLORS.down, noChangeColor: COLORS.noChange,
            upBorderColor: COLORS.up, downBorderColor: COLORS.down, noChangeBorderColor: COLORS.noChange,
            upWickColor: COLORS.up, downWickColor: COLORS.down, noChangeWickColor: COLORS.noChange,
          },
          priceMark: {
            show: true,
            high: { show: true, color: '#D9D9D9', textOffset: 5, textSize: 10, textFamily: 'Helvetica Neue', textWeight: 'normal' },
            low: { show: true, color: '#D9D9D9', textOffset: 5, textSize: 10, textFamily: 'Helvetica Neue', textWeight: 'normal' },
            last: {
              show: true,
              compareRule: 'current_open',
              upColor: COLORS.up, downColor: COLORS.down, noChangeColor: COLORS.noChange,
              line: { show: true, style: 'dashed', dashedValue: [4, 4], size: 1 },
              text: {
                show: true, style: 'fill', size: 12,
                paddingLeft: 4, paddingTop: 4, paddingRight: 4, paddingBottom: 4,
                borderStyle: 'solid', borderSize: 0, borderColor: 'transparent', borderDashedValue: [2, 2],
                color: '#FFFFFF', family: 'Helvetica Neue', weight: 'normal', borderRadius: 2,
              },
              extendTexts: [],
            },
          },
          tooltip: {
            showRule: 'always', showType: 'standard',
            offsetLeft: 4, offsetTop: 6, offsetRight: 4, offsetBottom: 6,
            title: { show: true, size: 14, family: 'Helvetica Neue', weight: 'normal', color: '#76808F', marginLeft: 8, marginTop: 4, marginRight: 8, marginBottom: 4 },
            legend: { size: 12, family: 'Helvetica Neue', weight: 'normal', color: '#76808F', marginLeft: 8, marginTop: 4, marginRight: 8, marginBottom: 4, defaultValue: 'n/a' },
            features: [],
          },
        },
        xAxis: {
          show: true,
          axisLine: { show: true, color: COLORS.grid, size: 1 },
          tickLine: { show: false },
          tickText: { show: true, color: '#76808F', size: 10, family: 'Helvetica Neue', weight: 'normal' },
        },
        yAxis: {
          show: true,
          axisLine: { show: true, color: COLORS.grid, size: 1 },
          tickLine: { show: false },
          tickText: { show: true, color: '#76808F', size: 10, family: 'Helvetica Neue', weight: 'normal' },
        },
        separator: { size: 1, color: COLORS.grid, fill: true, activeBackgroundColor: 'rgba(0,0,0,0)' },
      });

      chart.createIndicator('VOL', { isStack: false });

      chart.setDataLoader({
        getBars: ({ callback }) => {
          callback(chartData, false);
        },
      });

      if (symbol) {
        chart.setSymbol({ ticker: symbol });
      }
      chart.setPeriod({ span: 1, type: 'day' });
    }, 100);

    return () => {
      clearTimeout(timer);
    };
  }, [chartData, hasData, symbol]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (chartRef.current) {
        try { dispose(CHART_CONTAINER_ID); } catch { /* ignore */ }
        chartRef.current = null;
      }
    };
  }, []);

  const sourceLabel = data?.source ? SOURCE_LABELS[data.source] || data.source : '-';

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      <div className="relative rounded-2xl border border-slate-200 bg-white shadow-sm">
        {loading ? (
          <div className="flex h-[500px] items-center justify-center">
            <div className="flex flex-col items-center gap-3">
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
              <span className="text-sm text-slate-400">正在获取K线数据...</span>
            </div>
          </div>
        ) : error ? (
          <div className="flex h-[500px] items-center justify-center">
            <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 px-8 py-6 text-center">
              <p className="text-sm font-medium text-red-600">{error}</p>
            </div>
          </div>
        ) : (
          <>
            <div id={CHART_CONTAINER_ID} className="h-[500px] w-full" style={{ minHeight: 500 }} />
            <div className="flex items-center justify-between border-t border-slate-100 px-5 py-2">
              <div className="flex items-center gap-3 text-xs text-slate-500">
                <span className="font-mono font-semibold text-slate-700">{data?.symbol}</span>
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">日线 · 前复权</span>
                <span className="text-slate-400">{data?.count ?? '-'} 条数据</span>
              </div>
              <div className="flex items-center gap-1.5 text-xs text-slate-400">
                <span>
                  数据获取时间:{' '}
                  {data?._fetched_at ? new Date(data._fetched_at).toLocaleString('zh-CN') : '-'}
                  {data?._cached ? ' · 缓存' : ' · 实时'}
                </span>
                <span className="text-slate-300">|</span>
                <span>数据源: {sourceLabel}</span>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default KLineChartPanel;
