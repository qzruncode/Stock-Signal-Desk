import type { AgentStageEvent } from "../../utils/agentStage";
import { errorCode, recordValue, recordsFrom, text, toolName } from "./AgentReasoningBase";
import type { TraceRecord } from "./AgentReasoningBase";
import { argumentSummary, isRecoverablePlanningRetry } from "./AgentReasoningStageUtils";
import type { DetailLine } from "./AgentReasoningStageUtils";

export interface TimelineRow {
  key: string;
  event?: AgentStageEvent;
  result?: TraceRecord;
  kind: 'stage' | 'tool';
  modelTurn?: number;
  /** Render the Team worker lifecycle as a compact Plan-style status row. */
  compactTeamWorkerStatus?: boolean;
}

export interface TimelinePhase {
  key: string;
  index: number;
  modelTurn?: number;
  rows: TimelineRow[];
}

export const toolStatus = (result: TraceRecord | undefined, event: AgentStageEvent | undefined): AgentStageEvent['status'] => {
  if (result?.success === false) return 'failed';
  if (event?.status) return event.status;
  if (result?.success === true) return 'completed';
  return 'started';
};

export const toolSummary = (result: TraceRecord | undefined, event: AgentStageEvent | undefined): string => {
  const name = toolName(result)
    || text(recordValue(event?.details, 'tool_name', 'toolName'), 120)
    || event?.summary
    || '原子工具';
  const success = result?.success;
  if (errorCode(result) === 'approval_rejected') return `${name}：用户拒绝，未执行`;
  if (success === false) {
    const errors = Array.isArray(result?.errors)
      ? result.errors.map((value) => text(value)).filter(Boolean).join('；')
      : '';
    return `${name}：${errors || '调用未成功，模型可改用其他来源或收束回答'}`;
  }
  return event?.summary || `${name} 已返回`;
};

export interface ToolOutcomeCounts {
  running: number;
  completed: number;
  failed: number;
}

export const toolOutcomeCounts = (rows: TimelineRow[]): ToolOutcomeCounts => {
  return rows.reduce<ToolOutcomeCounts>((counts, row) => {
    const status = toolStatus(row.result, row.event);
    const problem = status === 'failed'
      || status === 'blocked'
      || status === 'cancelled'
      || Boolean(row.event?.errorCode)
      || Boolean(errorCode(row.result));
    if (status === 'started') counts.running += 1;
    else if (problem) counts.failed += 1;
    else counts.completed += 1;
    return counts;
  }, { running: 0, completed: 0, failed: 0 });
};

const positiveModelTurn = (value: unknown): number | undefined => {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
};

const rowModelTurn = (row: TimelineRow): number | undefined => (
  row.modelTurn ?? positiveModelTurn(recordValue(row.event?.details, 'model_turn', 'modelTurn'))
);

/** Group durable stage and tool events by the model turn that produced them. */
export const groupTimelinePhases = (rows: TimelineRow[]): TimelinePhase[] => {
  const groups = new Map<string, TimelinePhase>();
  let currentKey: string | undefined;
  let currentRunId: string | undefined;
  let fallbackIndex = 0;

  for (const row of rows) {
    const event = row.event;
    const eventRunId = event?.runId?.trim();
    if (eventRunId && currentRunId && eventRunId !== currentRunId) currentKey = undefined;
    if (eventRunId) currentRunId = eventRunId;
    const runPrefix = `run:${currentRunId || 'unknown'}`;
    const roundId = event?.roundId?.trim();
    const turn = rowModelTurn(row);
    let key: string;

    if (roundId) {
      key = `${runPrefix}:round:${roundId}`;
    } else if (event?.stage === 'model') {
      key = turn ? `${runPrefix}:turn:${turn}` : `${runPrefix}:fallback:${++fallbackIndex}`;
    } else if (turn) {
      key = `${runPrefix}:turn:${turn}`;
    } else {
      key = currentKey || `${runPrefix}:fallback:${++fallbackIndex}`;
    }

    currentKey = key;
    const phase = groups.get(key) || {
      key,
      index: groups.size + 1,
      modelTurn: turn,
      rows: [],
    };
    if (!phase.modelTurn && turn) phase.modelTurn = turn;
    phase.rows.push(row);
    groups.set(key, phase);
  }

  return [...groups.values()];
};

const genericToolSummary = (summary: string, name: string): boolean => {
  const normalized = summary.trim();
  return !normalized || normalized === `执行原子工具 ${name}`;
};

export const toolPartLabel = ({
  name,
  summary,
  waitingForApproval,
  problem,
  hasResult,
}: {
  name: string;
  summary: string;
  waitingForApproval: boolean;
  problem: boolean;
  hasResult: boolean;
}): string => {
  if (!genericToolSummary(summary, name)) return summary.trim();
  if (waitingForApproval) return `等待确认：${name}`;
  if (problem) return `${name} 执行失败`;
  if (hasResult) return `${name} 已完成`;
  return `正在执行 ${name}`;
};

const stringValues = (value: unknown, limit = 2_400): string[] => (
  Array.isArray(value) ? value.map((item) => text(item, limit)).filter(Boolean) : []
);

const validUrl = (value: string): string => {
  try {
    const parsed = new URL(value);
    return ['http:', 'https:'].includes(parsed.protocol) ? parsed.toString() : '';
  } catch {
    return '';
  }
};

export const toolDetails = (result: TraceRecord | undefined, event: AgentStageEvent | undefined): DetailLine[] => {
  const details = event?.details || {};
  const lines: DetailLine[] = [];
  const argumentsValue = recordValue(result, 'arguments') ?? recordValue(details, 'arguments');
  const request = argumentSummary(argumentsValue);
  if (request) lines.push({ key: 'request', text: `请求：${request}` });

  const evidenceId = text(recordValue(details, 'evidence_id', 'evidenceId'), 100);
  const dataTime = text(
    recordValue(result, 'data_time', 'dataTime')
    ?? recordValue(details, 'data_time', 'dataTime'),
    160,
  );
  const resultCountValue = recordValue(result, 'result_count', 'resultCount')
    ?? recordValue(details, 'result_count', 'resultCount');
  const resultCount = typeof resultCountValue === 'number' ? resultCountValue : null;
  const omittedValue = recordValue(result, 'omitted_result_count', 'omittedResultCount')
    ?? recordValue(details, 'omitted_result_count', 'omittedResultCount');
  const omitted = typeof omittedValue === 'number' ? omittedValue : 0;
  const sourceLabels = stringValues(
    recordValue(result, 'source_labels', 'sourceLabels')
    ?? recordValue(details, 'source_labels', 'sourceLabels'),
    320,
  );
  const sourceRefs = stringValues(recordValue(result, 'source_refs', 'sourceRefs'), 1_000);
  const resultItems = recordsFrom(
    recordValue(result, 'result_items', 'resultItems')
    ?? recordValue(details, 'result_items', 'resultItems'),
  );
  const referenceLinks = stringValues(
    recordValue(result, 'reference_links', 'referenceLinks')
    ?? recordValue(details, 'reference_links', 'referenceLinks'),
    1_000,
  );

  const urlRefs = [...new Set([...referenceLinks, ...sourceRefs].map(validUrl).filter(Boolean))];
  const urlHosts = new Set(urlRefs.map((url) => {
    try { return new URL(url).hostname; } catch { return ''; }
  }).filter(Boolean));
  const fallbackLabels = sourceRefs.filter((value) => !validUrl(value) && !urlHosts.has(value));
  const labels = [...new Set(sourceLabels.length > 0 ? sourceLabels : fallbackLabels)];
  const overview = [
    resultCount !== null ? `返回 ${resultCount} 条结果` : resultItems.length > 0 ? `展示 ${resultItems.length} 条结果` : '',
    labels.length > 0 ? `数据来源：${labels.join('、')}` : '',
    dataTime ? `数据时间：${dataTime}` : '',
    evidenceId ? `证据：${evidenceId}` : '',
    result?.partial === true ? '部分结果' : '',
    result?.reused === true ? '复用幂等结果' : '',
  ].filter(Boolean).join(' · ');
  if (overview) lines.push({ key: 'overview', text: `结果：${overview}` });

  resultItems.forEach((item, index) => {
    const title = text(recordValue(item, 'title'), 360) || `结果 ${index + 1}`;
    const source = text(recordValue(item, 'source'), 320);
    const publishedAt = text(recordValue(item, 'published_at', 'publishedAt'), 160);
    const summary = text(recordValue(item, 'summary'), 500);
    const attributes = recordsFrom(recordValue(item, 'attributes'))
      .map((attribute) => {
        const name = text(recordValue(attribute, 'name'), 80);
        const value = text(recordValue(attribute, 'value'), 160);
        return name && value ? `${name}=${value}` : '';
      })
      .filter(Boolean);
    const meta = [source, publishedAt, ...attributes, summary ? `摘要：${summary}` : '']
      .filter(Boolean).join(' · ');
    const href = validUrl(text(recordValue(item, 'url'), 1_000));
    lines.push({
      key: `result-${index}`,
      text: `${index + 1}. ${title}${meta ? ` · ${meta}` : ''}`,
      href: href || undefined,
    });
  });

  if (omitted > 0) {
    lines.push({ key: 'omitted', text: `其余 ${omitted} 条结果未放入过程面板，完整原始结果仍保留在运行检查点中。` });
  }
  const itemUrls = new Set(resultItems.map((item) => validUrl(text(recordValue(item, 'url'), 1_000))).filter(Boolean));
  if (resultItems.length === 0) {
    urlRefs.forEach((href, index) => lines.push({ key: `reference-${index}`, text: `引用 ${index + 1}`, href }));
  } else {
    urlRefs.filter((href) => !itemUrls.has(href)).forEach((href, index) => {
      lines.push({ key: `extra-reference-${index}`, text: `补充引用 ${index + 1}`, href });
    });
  }
  const resultSummary = text(
    recordValue(result, 'result_summary', 'resultSummary')
    ?? recordValue(details, 'result_summary', 'resultSummary'),
    800,
  );
  if (resultSummary) lines.push({ key: 'result-summary', text: `返回摘要：${resultSummary}` });
  const errors = stringValues(recordValue(result, 'errors') ?? recordValue(details, 'errors'), 2_400);
  errors.forEach((error, index) => lines.push({ key: `error-${index}`, text: `错误 ${index + 1}：${error}` }));
  return lines;
};

export const phaseStatus = (phase: TimelinePhase): AgentStageEvent['status'] => {
  const statuses = phase.rows.map((row) => {
    const status = row.kind === 'tool' ? toolStatus(row.result, row.event) : row.event?.status || 'started';
    return row.event && isRecoverablePlanningRetry(row.event) ? 'started' : status;
  });
  if (statuses.some((status) => status === 'failed' || status === 'blocked')) return 'failed';
  if (statuses.some((status) => status === 'cancelled')) return 'cancelled';
  if (statuses.some((status) => status === 'started')) return 'started';
  return statuses.at(-1) || 'completed';
};

export const phaseProblem = (phase: TimelinePhase): boolean => phase.rows.some((row) => {
  if (row.event && isRecoverablePlanningRetry(row.event)) return false;
  const status = row.kind === 'tool' ? toolStatus(row.result, row.event) : row.event?.status;
  return status === 'failed'
    || status === 'blocked'
    || status === 'cancelled'
    || Boolean(row.event?.errorCode)
    || Boolean(row.kind === 'tool' && errorCode(row.result));
});

export const phaseHasActiveDetails = (phase: TimelinePhase): boolean => phase.rows.some((row) => {
  if (row.kind === 'tool') return true;
  const stage = row.event?.stage;
  return Boolean(stage && stage !== 'model' && stage !== 'publish');
});

export const phaseHeadline = (phase: TimelinePhase): string => {
  const model = phase.rows.find((row) => row.kind === 'stage' && row.event?.stage === 'model');
  const modelProgress = text(recordValue(model?.event?.details, 'progress_preview'), 360);
  if (modelProgress) return modelProgress;
  if (model?.event?.summary) return model.event.summary;
  const first = phase.rows[0];
  if (first?.kind === 'tool') return toolSummary(first.result, first.event);
  return first?.event?.summary || '执行阶段';
};
