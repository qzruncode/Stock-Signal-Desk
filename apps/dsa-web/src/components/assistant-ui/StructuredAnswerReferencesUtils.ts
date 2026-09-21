import type {
  StructuredAnswerChartReference,
  StructuredAnswerProjection,
} from '../../api/agent';

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

const SAFE_CHART_KEY = /^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/;
const SAFE_CHART_ID = /^[A-Za-z0-9_.:-]{1,160}$/;

const comparableChartKey = (value: string): string => (
  value.replace(/[^A-Za-z0-9]/g, '').toLowerCase()
);

/** Normalize live assistant-stream data and camel-cased replay data alike. */
export const normalizeChartReference = (
  value: unknown,
): StructuredAnswerChartReference | null => {
  if (!isRecord(value)) return null;
  const chartId = String(value.chartId ?? value.chart_id ?? '').trim();
  if (!SAFE_CHART_ID.test(chartId)) return null;
  const chartType = String(value.chartType ?? value.chart_type ?? 'line').trim().toLowerCase();
  const rawSeries = value.series;
  if (!Array.isArray(rawSeries)) return null;
  const series = rawSeries
    .filter(isRecord)
    .map((item) => ({
      key: String(item.key ?? '').trim(),
      label: String(item.label ?? item.key ?? '').trim(),
    }))
    .filter((item) => SAFE_CHART_KEY.test(item.key))
    .slice(0, 3);
  if (series.length === 0 || !Array.isArray(value.data)) return null;
  const keys = new Set(series.map((item) => item.key));
  const xKey = typeof value.xKey === 'string'
    ? value.xKey
    : typeof value.x_key === 'string'
      ? value.x_key
      : 'x';
  const data = value.data.slice(0, 120).flatMap((rawPoint) => {
    if (!isRecord(rawPoint)) return [];
    const xValue = rawPoint.x ?? rawPoint[xKey] ?? rawPoint.x_key;
    if (typeof xValue !== 'string' || !xValue.trim()) return [];
    const point: Record<string, string | number> = { x: xValue.slice(0, 80) };
    for (const key of keys) {
      const sourceKey = Object.keys(rawPoint).find((candidate) => (
        candidate === key || comparableChartKey(candidate) === comparableChartKey(key)
      ));
      const number = sourceKey ? rawPoint[sourceKey] : undefined;
      if (typeof number === 'number' && Number.isFinite(number)) point[key] = number;
    }
    return Object.keys(point).length > 1 ? [point] : [];
  });
  if (data.length === 0) return null;
  return {
    chartId,
    chartType: chartType === 'bar' || chartType === 'area' ? chartType : 'line',
    title: String(value.title ?? '数据图表').trim().slice(0, 160),
    xKey: xKey.trim() || 'x',
    series,
    data,
    actionId: typeof (value.actionId ?? value.action_id) === 'string'
      ? String(value.actionId ?? value.action_id)
      : null,
    evidenceId: typeof (value.evidenceId ?? value.evidence_id) === 'string'
      ? String(value.evidenceId ?? value.evidence_id)
      : null,
  };
};

/** Read the persisted typed answer without trusting arbitrary trace fields. */
export const structuredAnswerFromTrace = (
  value: unknown,
): StructuredAnswerProjection | null => {
  if (!isRecord(value)) return null;
  const raw = value.structuredAnswer ?? value.structured_answer;
  if (!isRecord(raw) || !Array.isArray(raw.blocks) || raw.blocks.length === 0) return null;
  return raw as unknown as StructuredAnswerProjection;
};

const markdownLabel = (value: unknown): string => (
  String(value ?? '')
    .slice(0, 160)
    .replace(/[\\[\]()]/g, '\\$&')
);

/**
 * The server adds plain Markdown reference lines as a streaming-safe fallback.
 * Once the typed projection is available, render those references as cards
 * instead of showing the same artifact/chart/action twice.
 */
export const stripStructuredAnswerReferenceFallbacks = (
  text: string,
  answer: StructuredAnswerProjection | null,
): string => {
  if (!answer || !Array.isArray(answer.blocks) || !text) return text;

  const fallbackLines = new Set<string>();
  const hasCharts = answer.blocks.some((block) => (
    Array.isArray(block.chartRefs) && block.chartRefs.length > 0
  ));
  for (const block of answer.blocks) {
    for (const reference of block.artifactRefs ?? []) {
      if (reference.downloadUrl) fallbackLines.add(`- [下载文件：${markdownLabel(reference.title || reference.artifactId)}](${reference.downloadUrl})`);
    }
    for (const reference of block.chartRefs ?? []) {
      fallbackLines.add(`- 图表：${markdownLabel(reference.title || '数据图表')}（已根据本轮工具数据生成）`);
    }
    for (const reference of block.actionRefs ?? []) {
      const status = reference.status === 'completed' ? '已完成' : '失败';
      const toolName = markdownLabel(reference.toolName || '工具动作');
      fallbackLines.add(`- 动作记录：${toolName}（${status}，仅展示，不会再次执行）`);
    }
  }

  if (fallbackLines.size === 0) return text;
  return text
    .split('\n')
    .filter((line) => {
      const trimmed = line.trim();
      if (fallbackLines.has(trimmed)) return false;
      // Chart titles from older traces may be machine labels while the card
      // now presents a human label.  The fallback marker itself is stable.
      return !(hasCharts && /^- 图表：.+（已根据本轮工具数据生成）$/.test(trimmed));
    })
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
};

/**
 * Native answer parts can arrive before the terminal trace metadata is
 * attached. Remove only the server-generated reference markers in that
 * window, while preserving a following partial-run diagnostic.
 */
export const stripNativeAnswerReferenceFallbacks = (text: string): string => (
  text
    .replace(/^- 图表：.+?（已根据本轮工具数据生成）/gm, '')
    .replace(/^- 动作记录：.+?（(?:已完成|失败)，仅展示，不会再次执行）/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
);
