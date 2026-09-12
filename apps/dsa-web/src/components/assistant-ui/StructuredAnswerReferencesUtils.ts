import type {
  StructuredAnswerProjection,
} from '../../api/agent';

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

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
