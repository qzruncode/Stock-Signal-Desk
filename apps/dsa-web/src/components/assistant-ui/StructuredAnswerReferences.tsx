import type { FC } from 'react';
import { BarChart3, ClipboardList, Download, FileText } from 'lucide-react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type {
  StructuredAnswerActionReference,
  StructuredAnswerArtifactReference,
  StructuredAnswerChartReference,
  StructuredAnswerProjection,
} from '../../api/agent';
import { cn } from '../../utils/cn';

const SAFE_ARTIFACT_URL = /^\/api\/v1\/agent\/(?:exports\/(?:stock-screen(?:-financial)?|atr-volatility)-\d{8}-\d{6}-[0-9a-f]{8}\.csv|resources\/textdoc_[0-9a-f]{40}\/content\?disposition=attachment)$/;
const SAFE_CHART_KEY = /^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/;

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

const safeDownloadUrl = (value: unknown): string | null => (
  typeof value === 'string' && SAFE_ARTIFACT_URL.test(value) ? value : null
);

const safeSeries = (reference: StructuredAnswerChartReference) => (
  (Array.isArray(reference.series) ? reference.series : []).filter((item) => (
    isRecord(item)
    && typeof item.key === 'string'
    && SAFE_CHART_KEY.test(item.key)
  )).slice(0, 3)
);

const safeChartData = (
  reference: StructuredAnswerChartReference,
  series: Array<{ key: string; label: string }>,
) => {
  const seriesKeys = new Set(series.map((item) => item.key));
  return (Array.isArray(reference.data) ? reference.data : []).filter(isRecord).slice(0, 120).flatMap((item) => {
    if (typeof item.x !== 'string' || !item.x.trim()) return [];
    const point: Record<string, string | number> = { x: item.x.slice(0, 80) };
    for (const key of seriesKeys) {
      const value = item[key];
      if (typeof value === 'number' && Number.isFinite(value)) point[key] = value;
    }
    return Object.keys(point).length > 1 ? [point] : [];
  });
};

const SERIES_COLORS = ['hsl(var(--primary))', 'hsl(var(--cyan))', 'hsl(var(--warning))'];
const MACHINE_CHART_TITLE = /^[A-Za-z0-9_.:-]{2,120}数据$/;
const SERIES_LABELS: Record<string, string> = {
  current_atr_pct: 'ATR相对波动率(%)',
  close: '收盘价',
  high: '最高价',
  low: '最低价',
  main_net_inflow: '主力净流入',
  medium_net_inflow: '中单净流入',
  large_net_inflow: '大单净流入',
  small_net_inflow: '小单净流入',
  super_large_net_inflow: '超大单净流入',
  volume: '成交量',
};

const seriesLabel = (item: { key: string; label: string }): string => (
  SERIES_LABELS[item.key] || item.label || item.key
);

const chartTitle = (reference: StructuredAnswerChartReference): string => {
  if (reference.title && !MACHINE_CHART_TITLE.test(reference.title)) return reference.title;
  const firstSeries = safeSeries(reference)[0];
  return (firstSeries && SERIES_LABELS[firstSeries.key]) || '数据图表';
};

const formatChartTick = (value: number): string => {
  const absolute = Math.abs(value);
  const units: Array<[number, string]> = [
    [100_000_000, '亿'],
    [10_000, '万'],
    [1_000, '千'],
  ];
  const unit = units.find(([threshold]) => absolute >= threshold);
  if (!unit) return String(Math.round(value));
  const scaled = value / unit[0];
  const digits = Math.abs(scaled) >= 100 ? 0 : Math.abs(scaled) >= 10 ? 1 : 2;
  return `${scaled.toFixed(digits).replace(/\.0+$|(?<=\.\d)0+$/g, '')}${unit[1]}`;
};

export const ChartReference: FC<{ reference: StructuredAnswerChartReference }> = ({ reference }) => {
  const series = safeSeries(reference);
  const data = safeChartData(reference, series);
  if (series.length === 0 || data.length === 0) return null;

  const axes = (
    <>
      <CartesianGrid strokeDasharray="3 3" className="opacity-40" />
      <XAxis dataKey="x" tick={{ fontSize: 10 }} tickLine={false} axisLine={false} />
      <YAxis tick={{ fontSize: 10 }} tickFormatter={formatChartTick} tickLine={false} axisLine={false} width={48} />
      <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
    </>
  );

  const chart = reference.chartType === 'bar' ? (
    <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
      {axes}
      {series.map((item, index) => (
        <Bar key={item.key} dataKey={item.key} fill={SERIES_COLORS[index]} radius={[3, 3, 0, 0]} />
      ))}
    </BarChart>
  ) : reference.chartType === 'area' ? (
    <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
      {axes}
      {series.map((item, index) => (
        <Area key={item.key} type="monotone" dataKey={item.key} stroke={SERIES_COLORS[index]} fill={SERIES_COLORS[index]} fillOpacity={0.12} />
      ))}
    </AreaChart>
  ) : (
    <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
      {axes}
      {series.map((item, index) => (
        <Line key={item.key} type="monotone" dataKey={item.key} stroke={SERIES_COLORS[index]} strokeWidth={2} dot={false} />
      ))}
    </LineChart>
  );

  return (
    <div className="mt-2 rounded-lg border border-border/70 bg-card/70 p-2.5" data-structured-answer-chart={reference.chartId}>
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-foreground">
        <BarChart3 className="size-3.5 text-primary" />
        <span>{chartTitle(reference)}</span>
      </div>
      <div className="h-56 w-full min-w-0">
        <ResponsiveContainer
          width="100%"
          height="100%"
          minWidth={240}
          minHeight={224}
          initialDimension={{ width: 640, height: 224 }}
        >
          {chart}
        </ResponsiveContainer>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-secondary-text">
        {series.map((item, index) => (
          <span key={item.key} className="inline-flex items-center gap-1">
            <span className="size-1.5 rounded-full" style={{ backgroundColor: SERIES_COLORS[index] }} />
            {seriesLabel(item)}
          </span>
        ))}
      </div>
    </div>
  );
};

const ArtifactReference: FC<{ reference: StructuredAnswerArtifactReference }> = ({ reference }) => {
  const href = safeDownloadUrl(reference.downloadUrl);
  if (!href) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      download
      className="flex items-center justify-between gap-3 rounded-lg border border-border/70 bg-card/70 px-3 py-2 text-xs transition hover:border-primary/40 hover:bg-primary/[0.035]"
      data-structured-answer-artifact={reference.artifactId}
    >
      <span className="flex min-w-0 items-center gap-2">
        <FileText className="size-3.5 shrink-0 text-primary" />
        <span className="truncate text-foreground" title={reference.title}>{reference.title || reference.artifactId}</span>
      </span>
      <span className="inline-flex shrink-0 items-center gap-1 text-[10px] text-primary">
        <Download className="size-3" /> 下载
      </span>
    </a>
  );
};

const ActionReference: FC<{ reference: StructuredAnswerActionReference }> = ({ reference }) => (
  <div className="flex items-center gap-2 rounded-lg border border-border/70 bg-muted/30 px-3 py-2 text-[11px]" data-structured-answer-action={reference.actionId}>
    <ClipboardList className="size-3.5 shrink-0 text-secondary-text" />
    <span className="min-w-0 truncate text-foreground" title={reference.toolName || reference.actionId}>
      {reference.toolName || '工具动作'}
    </span>
    <span className="max-w-32 truncate font-mono text-[10px] text-secondary-text" title={reference.actionId}>
      {reference.actionId}
    </span>
    <span className={cn('ml-auto shrink-0', reference.success ? 'text-success' : 'text-warning')}>
      {reference.success ? '已完成' : '未完成'} · 仅展示
    </span>
  </div>
);

export const StructuredAnswerReferences: FC<{
  answer: StructuredAnswerProjection | null;
  renderedText?: string;
  renderCharts?: boolean;
  renderActions?: boolean;
}> = ({ answer, renderedText = '', renderCharts = true, renderActions = true }) => {
  if (!answer) return null;
  const blocks = Array.isArray(answer.blocks)
    ? answer.blocks.filter(isRecord)
    : [];
  const artifacts = blocks.flatMap((block) => (
    Array.isArray(block.artifactRefs) ? block.artifactRefs : []
  )).filter((reference) => !renderedText.includes(reference.downloadUrl)).slice(0, 24);
  const charts = renderCharts
    ? blocks.flatMap((block) => (
      Array.isArray(block.chartRefs) ? block.chartRefs : []
    )).slice(0, 24)
    : [];
  const actions = renderActions
    ? blocks.flatMap((block) => (
      Array.isArray(block.actionRefs) ? block.actionRefs : []
    )).slice(0, 32)
    : [];
  if (artifacts.length === 0 && charts.length === 0 && actions.length === 0) return null;

  return (
    <div className="mt-3 space-y-2" data-structured-answer-references>
      {artifacts.map((reference) => <ArtifactReference key={reference.artifactId} reference={reference} />)}
      {charts.map((reference) => <ChartReference key={reference.chartId} reference={reference} />)}
      {actions.map((reference) => <ActionReference key={reference.actionId} reference={reference} />)}
    </div>
  );
};
