import type { FC } from 'react';
import { useId, useMemo, useState } from 'react';
import { useMessage, useMessageTiming } from '@assistant-ui/react';
import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import {
  BookOpenIcon,
  CheckCircle2Icon,
  CircleAlertIcon,
  ChevronRightIcon,
  Loader2Icon,
} from 'lucide-react';
import {
  agentStageDurationMs,
  agentStageEvents,
  agentStageLabel,
  reconcileTerminalStageEvents,
  type AgentStageEvent,
  type AgentStageEventV2,
} from '../../utils/agentStage';
import { cn } from '../../utils/cn';
import { formatElapsedDuration } from '../../utils/format';
import { AssistantMarkdown } from './AssistantMarkdownText';

type TraceRecord = Record<string, unknown>;

const isRecord = (value: unknown): value is TraceRecord => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

const recordsFrom = (value: unknown): TraceRecord[] => (
  Array.isArray(value) ? value.filter(isRecord) : []
);

const recordValue = (record: TraceRecord | undefined, ...keys: string[]): unknown => {
  if (!record) return undefined;
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return undefined;
};

const text = (value: unknown, limit = 2_400): string => {
  if (typeof value === 'string') return value.slice(0, limit);
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
};

const id = (value: unknown): string => typeof value === 'string' ? value : '';

const actionId = (record: TraceRecord | undefined): string => id(
  recordValue(record, 'action_id', 'actionId', 'id'),
);

const toolName = (record: TraceRecord | undefined): string => text(
  recordValue(record, 'tool_name', 'toolName'),
  120,
);

const errorCode = (record: TraceRecord | undefined): string => text(
  recordValue(record, 'error_code', 'errorCode'),
  120,
);

const stageKey = (event: AgentStageEvent): string => [
  event.runId,
  event.stage,
  event.actionId || '',
  event.toolCallId || '',
  event.occurredAt || '',
  event.summary,
].join('|');

const StatusIcon: FC<{
  status: AgentStageEvent['status'];
  problem?: boolean;
  className?: string;
}> = ({ status, problem = false, className }) => {
  if (problem || status === 'failed' || status === 'blocked' || status === 'cancelled') {
    return <CircleAlertIcon className={className} />;
  }
  if (status === 'completed' || status === 'succeeded') {
    return <CheckCircle2Icon className={className} />;
  }
  return <Loader2Icon className={className} />;
};

const statusText = (status: AgentStageEvent['status'], problem = false): string => {
  if (problem || status === 'failed' || status === 'blocked') return '有缺口';
  if (status === 'cancelled') return '已取消';
  if (status === 'started') return '进行中';
  return '已完成';
};

interface DetailLine {
  key: string;
  text: string;
  href?: string;
}

const detailValue = (value: unknown): string => {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (value === null || value === undefined) return '';
  try {
    return JSON.stringify(value, null, 0);
  } catch {
    return String(value);
  }
};

const argumentSummary = (value: unknown): string => {
  if (!isRecord(value)) return '';
  return Object.entries(value)
    .map(([key, item]) => `${key}=${detailValue(item)}`)
    .join(' · ');
};

const stageDetails = (event: AgentStageEvent): DetailLine[] => {
  const details = event.details || {};
  if (event.stage === 'model') {
    const operations = recordsFrom(recordValue(details, 'operations'));
    const progress = text(recordValue(details, 'progress_preview'), 2_400);
    return [
      ...(progress ? [{ key: 'progress', text: `过程说明：${progress}` }] : []),
      ...operations.map((operation, index) => {
          const name = toolName(operation) || '原子操作';
          const argumentsText = argumentSummary(recordValue(operation, 'arguments'));
          const argumentKeys = recordValue(operation, 'argument_keys', 'argumentKeys');
          const keys = Array.isArray(argumentKeys)
            ? argumentKeys.map((value) => text(value, 96)).filter(Boolean)
            : [];
          const request = argumentsText || (keys.length > 0 ? `参数字段：${keys.join('、')}` : '无参数');
          return { key: `operation-${index}`, text: `操作 ${index + 1}：${name} · ${request}` };
        }),
    ];
  }
  if (event.stage === 'evidence') {
    const evidenceIds = recordValue(details, 'evidence_ids', 'evidenceIds');
    const ids = Array.isArray(evidenceIds)
      ? evidenceIds.map((value) => text(value, 100)).filter(Boolean).join('、')
      : '';
    const rawIssues = recordValue(details, 'issues');
    const issues = Array.isArray(rawIssues)
      ? rawIssues.map((value) => text(value)).filter(Boolean)
      : '';
    const claimCountValue = recordValue(details, 'claim_count', 'claimCount');
    const factCountValue = recordValue(details, 'fact_claim_count', 'factClaimCount');
    const inferenceCountValue = recordValue(details, 'inference_claim_count', 'inferenceClaimCount');
    const claimCount = typeof claimCountValue === 'number' ? claimCountValue : 0;
    const factCount = typeof factCountValue === 'number' ? factCountValue : 0;
    const inferenceCount = typeof inferenceCountValue === 'number' ? inferenceCountValue : 0;
    const audit = claimCount > 0
      ? `${claimCount} 条结论（事实 ${factCount}、推断 ${inferenceCount}）`
      : '';
    return [
      ...(audit ? [{ key: 'audit', text: `校验范围：${audit}` }] : []),
      ...(ids ? [{ key: 'evidence-ids', text: `已关联证据：${ids}` }] : []),
      ...(Array.isArray(issues)
        ? issues.map((issue, index) => ({ key: `issue-${index}`, text: `缺口 ${index + 1}：${issue}` }))
        : []),
    ];
  }
  if (event.stage === 'approval') {
    const name = text(recordValue(details, 'tool_name', 'toolName'), 120);
    const args = argumentSummary(recordValue(details, 'arguments'));
    return [{ key: 'approval', text: [name, args ? `请求参数：${args}` : ''].filter(Boolean).join(' · ') }];
  }
  if (event.stage === 'publish') return [];
  const preview = text(recordValue(details, 'answer_preview', 'answerPreview'));
  return preview ? [{ key: 'preview', text: `回答预览：${preview}` }] : [];
};

interface TimelineRow {
  key: string;
  event?: AgentStageEvent;
  result?: TraceRecord;
  kind: 'stage' | 'tool';
  modelTurn?: number;
}

interface TimelinePhase {
  key: string;
  index: number;
  modelTurn?: number;
  rows: TimelineRow[];
}

const toolStatus = (result: TraceRecord | undefined, event: AgentStageEvent | undefined): AgentStageEvent['status'] => {
  if (result?.success === false) return 'failed';
  if (event?.status) return event.status;
  if (result?.success === true) return 'completed';
  return 'started';
};

const toolSummary = (result: TraceRecord | undefined, event: AgentStageEvent | undefined): string => {
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

const positiveModelTurn = (value: unknown): number | undefined => {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
};

const rowModelTurn = (row: TimelineRow): number | undefined => (
  row.modelTurn ?? positiveModelTurn(recordValue(row.event?.details, 'model_turn', 'modelTurn'))
);

/**
 * Group the durable event stream into model-turn phases.
 *
 * A phase is deliberately wider than a tool call: it contains one model
 * decision, all tools selected by that decision (including parallel tools),
 * and the resulting evidence/publication events.  ``roundId`` is the stable
 * identity for new runs.  The model-turn/order fallback keeps old persisted
 * runs readable without guessing from human-facing summaries.
 */
const groupTimelinePhases = (rows: TimelineRow[]): TimelinePhase[] => {
  const groups = new Map<string, TimelinePhase>();
  let currentKey: string | undefined;
  let currentRunId: string | undefined;
  let legacyIndex = 0;

  for (const row of rows) {
    const event = row.event;
    const eventRunId = event?.runId?.trim();
    if (eventRunId && currentRunId && eventRunId !== currentRunId) {
      // A conversation can contain a recovered/retried run whose model-turn
      // counter starts again at one.  Never merge that phase with the prior
      // run just because both rounds have the same number.
      currentKey = undefined;
    }
    if (eventRunId) currentRunId = eventRunId;
    const runPrefix = `run:${currentRunId || 'unknown'}`;
    const roundId = event?.roundId?.trim();
    const turn = rowModelTurn(row);
    let key: string;

    if (roundId) {
      key = `${runPrefix}:round:${roundId}`;
    } else if (event?.stage === 'model') {
      key = turn ? `${runPrefix}:turn:${turn}` : `${runPrefix}:legacy:${++legacyIndex}`;
    } else if (turn) {
      key = `${runPrefix}:turn:${turn}`;
    } else {
      key = currentKey || `${runPrefix}:legacy:${++legacyIndex}`;
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

const toolPartLabel = ({
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

const toolDetails = (result: TraceRecord | undefined, event: AgentStageEvent | undefined): DetailLine[] => {
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
    const meta = [
      source,
      publishedAt,
      ...attributes,
      summary ? `摘要：${summary}` : '',
    ].filter(Boolean).join(' · ');
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
    urlRefs.forEach((href, index) => {
      lines.push({ key: `reference-${index}`, text: `引用 ${index + 1}`, href });
    });
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

const TimelineDetailLines: FC<{ lines: DetailLine[] }> = ({ lines }) => {
  if (lines.length === 0) return null;
  return (
    <div className="mt-1 space-y-0.5 text-[11px] leading-5 text-muted-foreground/85">
      {lines.map((detail) => (
        <div key={detail.key} className="whitespace-pre-wrap break-words">
          <span>{detail.text}</span>
          {detail.href ? (
            <>
              <span> · </span>
              <a
                href={detail.href}
                target="_blank"
                rel="noreferrer"
                className="break-all text-primary underline-offset-2 hover:underline"
              >
                {detail.href}
              </a>
            </>
          ) : null}
        </div>
      ))}
    </div>
  );
};

const TimelineStageRow: FC<{ row: TimelineRow }> = ({ row }) => {
  const event = row.event;
  if (!event) return null;
  const status = event.status;
  const problem = status === 'failed' || status === 'blocked' || status === 'cancelled'
    || Boolean(event.errorCode);
  if (event.stage === 'model') {
    const progress = text(recordValue(event.details, 'progress_preview'), 2_400);
    return (
      <li className="min-w-0 py-1.5 text-sm text-foreground/90">
        {progress ? (
          <AssistantMarkdown text={progress} />
        ) : (
          <div className={cn(
            'whitespace-pre-wrap break-words leading-6',
            problem ? 'text-amber-700' : 'text-foreground/90',
          )}>
            {event.summary || '模型继续处理当前阶段'}
          </div>
        )}
      </li>
    );
  }
  const label = agentStageLabel(event.stage);
  const details = stageDetails(event);
  return (
    <li className="flex min-w-0 items-start gap-2 py-1.5 text-xs">
      <StatusIcon
        status={status}
        problem={problem}
        className={cn(
          'mt-0.5 size-3.5 shrink-0',
          problem
            ? 'text-amber-600'
            : status === 'completed' || status === 'succeeded'
              ? 'text-emerald-600'
              : 'animate-spin text-primary',
        )}
      />
      <div className="min-w-0 flex-1 leading-5">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="font-medium text-foreground">{label}</span>
          <span className="text-muted-foreground">{statusText(status, problem)}</span>
        </div>
        {event.summary && event.stage !== 'model' ? (
          <div className="whitespace-pre-wrap break-words text-muted-foreground">{event.summary}</div>
        ) : null}
        <TimelineDetailLines lines={details} />
      </div>
    </li>
  );
};

const TimelineToolRow: FC<{ row: TimelineRow }> = ({ row }) => {
  const event = row.event;
  const name = toolName(row.result)
    || text(recordValue(event?.details, 'tool_name', 'toolName'), 120)
    || '原子工具';
  const status = toolStatus(row.result, event);
  const problem = status === 'failed' || status === 'blocked' || status === 'cancelled'
    || Boolean(event?.errorCode)
    || errorCode(row.result) !== '';
  const summary = toolSummary(row.result, event);
  const details = toolDetails(row.result, event);
  const detailId = useId();
  const [expanded, setExpanded] = useState(false);
  return (
    <li className="min-w-0 border-t border-primary/10 first:border-t-0">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={detailId}
        aria-label={`${expanded ? '收起' : '展开'}工具 ${name} 详情`}
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full min-w-0 items-start gap-2 py-2 text-left text-xs transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
      >
        <StatusIcon
          status={status}
          problem={problem}
          className={cn(
            'mt-0.5 size-3.5 shrink-0',
            problem
              ? 'text-amber-600'
              : status === 'completed' || status === 'succeeded'
                ? 'text-emerald-600'
                : 'animate-spin text-primary',
          )}
        />
        <span className="min-w-0 flex-1 leading-5">
          <span className="font-medium text-foreground">{name}</span>
          <span className="ml-2 text-muted-foreground">{statusText(status, problem)}</span>
          <span className="ml-2 whitespace-pre-wrap break-words text-muted-foreground">{summary}</span>
        </span>
        <ChevronRightIcon
          className={cn(
            'mt-0.5 size-3.5 shrink-0 text-muted-foreground transition-transform duration-300 ease-out',
            expanded && 'rotate-90',
          )}
          aria-hidden="true"
        />
      </button>
      <div
        id={detailId}
        role="region"
        aria-label={`${name}工具详情`}
        aria-hidden={!expanded}
        className="grid transition-[grid-template-rows] duration-300 ease-out"
        style={{ gridTemplateRows: expanded ? '1fr' : '0fr' }}
      >
        <div className="min-h-0 overflow-hidden">
          <div className={cn(
            'pb-2 pl-5 pr-5 text-muted-foreground transition-opacity duration-300 ease-out',
            expanded ? 'opacity-100' : 'opacity-0',
          )}>
            {details.length > 0
              ? <TimelineDetailLines lines={details} />
              : <div className="text-[11px] leading-5">该工具未提供结构化详情。</div>}
          </div>
        </div>
      </div>
    </li>
  );
};

const phaseStatus = (phase: TimelinePhase): AgentStageEvent['status'] => {
  const statuses = phase.rows.map((row) => row.kind === 'tool'
    ? toolStatus(row.result, row.event)
    : row.event?.status || 'started');
  if (statuses.some((status) => status === 'failed' || status === 'blocked')) return 'failed';
  if (statuses.some((status) => status === 'cancelled')) return 'cancelled';
  if (statuses.some((status) => status === 'started')) return 'started';
  return statuses.at(-1) || 'completed';
};

const phaseProblem = (phase: TimelinePhase): boolean => phase.rows.some((row) => {
  const status = row.kind === 'tool' ? toolStatus(row.result, row.event) : row.event?.status;
  return status === 'failed'
    || status === 'blocked'
    || status === 'cancelled'
    || Boolean(row.event?.errorCode)
    || Boolean(row.kind === 'tool' && errorCode(row.result));
});

const phaseHasActiveDetails = (phase: TimelinePhase): boolean => phase.rows.some((row) => {
  if (row.kind === 'tool') return true;
  const stage = row.event?.stage;
  return Boolean(stage && stage !== 'model' && stage !== 'publish');
});

const phaseHeadline = (phase: TimelinePhase): string => {
  const model = phase.rows.find((row) => row.kind === 'stage' && row.event?.stage === 'model');
  const modelProgress = text(recordValue(model?.event?.details, 'progress_preview'), 360);
  if (modelProgress) return modelProgress;
  if (model?.event?.summary) return model.event.summary;
  const first = phase.rows[0];
  if (first?.kind === 'tool') return toolSummary(first.result, first.event);
  return first?.event?.summary || '执行阶段';
};

const TimelinePhaseRow: FC<{
  phase: TimelinePhase;
}> = ({ phase }) => {
  const [expandedOverride, setExpandedOverride] = useState(false);
  const detailId = useId();
  const expanded = expandedOverride;
  const status = phaseStatus(phase);
  const problem = phaseProblem(phase);
  const toolRows = phase.rows.filter((row) => row.kind === 'tool');
  const phaseNumber = phase.modelTurn || phase.index;
  const phaseLabel = `第 ${phaseNumber} 阶段`;
  const headline = phaseHeadline(phase);
  const phaseSummary = toolRows.length > 0
    ? `${status === 'started' ? '正在执行' : problem ? '执行出现问题' : '已完成'} ${toolRows.length} 个工具`
    : headline;

  return (
    <li className="min-w-0 border-b border-border/60 last:border-b-0">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={detailId}
        aria-label={`${expanded ? '收起' : '展开'}${phaseLabel}`}
        onClick={() => setExpandedOverride((value) => !value)}
        className="flex w-full min-w-0 items-center gap-2 py-2 text-left text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
      >
        <StatusIcon
          status={status}
          problem={problem}
          className={cn(
            'size-4 shrink-0',
            problem
              ? 'text-amber-600'
              : status === 'completed' || status === 'succeeded'
                ? 'text-emerald-600'
                : 'animate-spin text-primary',
          )}
        />
        <span className="min-w-0 flex-1 truncate" title={headline}>
          <span className="font-medium text-foreground">{phaseLabel}</span>
          <span className="ml-2 text-muted-foreground">{phaseSummary}</span>
        </span>
        {toolRows.length > 0 ? <span className="shrink-0 text-xs">{toolRows.length} 个工具</span> : null}
        <ChevronRightIcon
          className={cn(
            'size-4 shrink-0 text-muted-foreground transition-transform duration-300 ease-out',
            expanded && 'rotate-90',
          )}
          aria-hidden="true"
        />
      </button>
      <div
        id={detailId}
        role="region"
        aria-label={`${phaseLabel}详情`}
        aria-hidden={!expanded}
        className="grid transition-[grid-template-rows] duration-300 ease-out"
        style={{ gridTemplateRows: expanded ? '1fr' : '0fr' }}
      >
        <div className="min-h-0 overflow-hidden">
          <div className={cn(
            'pl-2 transition-opacity duration-300 ease-out',
            expanded ? 'opacity-100' : 'opacity-0',
          )}>
            <ol className="space-y-0.5">
              {phase.rows.map((row) => row.kind === 'tool'
                ? <TimelineToolRow key={row.key} row={row} />
                : <TimelineStageRow key={row.key} row={row} />)}
            </ol>
          </div>
        </div>
      </div>
    </li>
  );
};

/**
 * Render the native assistant-ui tool part used by the live message stream.
 *
 * The backend emits tool-call parts through assistant-stream. Keeping this
 * renderer at the part boundary lets assistant-ui preserve the actual
 * ``model text -> tool -> model text`` order instead of reconstructing it from
 * separately accumulated stage metadata. The durable stage event is used only
 * for the safe, user-facing summary; raw tool arguments/results stay out of
 * this live fallback.
 */
export const AgentToolCallPart: FC<ToolCallMessagePartProps> = ({
  toolName: name,
  toolCallId,
  args,
  status,
  result,
  isError,
  approval,
}) => {
  const stageData = useMessage((message) => message.metadata?.unstable_data);
  const event = useMemo(
    () => agentStageEvents(stageData)
      .filter((candidate) => (
        candidate.toolCallId === toolCallId || candidate.actionId === toolCallId
      ))
      .at(-1) ?? null,
    [stageData, toolCallId],
  );
  const resultFailed = isRecord(result) && result.success === false;
  const waitingForApproval = Boolean(
    approval && approval.approved === undefined && !approval.resolution,
  );
  const problem = Boolean(isError) || resultFailed || Boolean(event?.errorCode)
    || event?.status === 'failed'
    || event?.status === 'blocked'
    || event?.status === 'cancelled';
  const hasResult = status.type === 'complete' || result !== undefined;
  const summary = waitingForApproval
    ? '该操作需要用户确认后才能继续'
    : event?.summary
      || (hasResult ? `${name} 已返回` : `执行原子工具 ${name}`);
  const label = toolPartLabel({
    name,
    summary,
    waitingForApproval,
    problem,
    hasResult,
  });
  const detailLines = useMemo(() => {
    const request = argumentSummary(args);
    const nativeResult = isRecord(result) ? result : undefined;
    return [
      ...(request ? [{ key: 'request', text: `请求：${request}` }] : []),
      ...toolDetails(nativeResult, event ?? undefined),
    ];
  }, [args, event, result]);
  const detailId = useId();
  const [expanded, setExpanded] = useState(false);
  const ToolIcon = problem || waitingForApproval
    ? CircleAlertIcon
    : hasResult
      ? BookOpenIcon
      : Loader2Icon;

  return (
    <div className="min-w-0 border-b border-border/60 last:border-b-0">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={detailId}
        aria-label={`${expanded ? '收起' : '展开'}工具 ${name} 详情`}
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full min-w-0 items-start gap-2 py-2 text-left text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
      >
        <ToolIcon
          className={cn(
            'mt-0.5 size-4 shrink-0',
            problem || waitingForApproval
              ? 'text-amber-600'
              : hasResult
                ? 'text-muted-foreground'
                : 'animate-spin text-primary',
          )}
        />
        <span className="min-w-0 flex-1 whitespace-pre-wrap break-words leading-5">{label}</span>
        <ChevronRightIcon
          className={cn(
            'mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform duration-300 ease-out',
            expanded && 'rotate-90',
          )}
          aria-hidden="true"
        />
      </button>
      <div
        id={detailId}
        role="region"
        aria-label={`${name}工具详情`}
        aria-hidden={!expanded}
        className="grid transition-[grid-template-rows] duration-300 ease-out"
        style={{ gridTemplateRows: expanded ? '1fr' : '0fr' }}
      >
        <div className="min-h-0 overflow-hidden">
          <div className={cn(
            'pb-2 pl-6 text-xs text-muted-foreground transition-opacity duration-300 ease-out',
            expanded ? 'opacity-100' : 'opacity-0',
          )}>
            {detailLines.length > 0
              ? <TimelineDetailLines lines={detailLines} />
              : <div className="leading-5">暂无结构化详情</div>}
          </div>
        </div>
      </div>
    </div>
  );
};

const visibleReasoningText = (rawText: string): string => (
  rawText.length > 12_000 ? `${rawText.slice(-12_000)}\n[较早过程已截断]` : rawText
);

export const AgentExecutionTimeline: FC<{
  reasoningText?: string;
  processText?: string;
  /** Legacy traces have no native parts; render them inline while they are
   * being upgraded instead of hiding the whole run behind one process card. */
  presentation?: 'inline' | 'disclosure';
}> = ({ reasoningText = '', processText = '', presentation = 'disclosure' }) => {
  const messageStatus = useMessage((state) => state.status?.type);
  const messageActive = messageStatus === 'running' || messageStatus === 'requires-action';
  const messageTiming = useMessageTiming();
  const stageData = useMessage((state) => state.metadata?.unstable_data);
  const traceData = useMessage((state) => (
    state.metadata?.custom?.agent_execution_trace
    ?? state.metadata?.custom?.agentExecutionTrace
  ));
  const events = useMemo(() => {
    const seen = new Set<string>();
    return reconcileTerminalStageEvents(agentStageEvents(stageData)).filter((event) => {
      const key = stageKey(event);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [stageData]);
  const trace = useMemo(() => isRecord(traceData) ? traceData : {}, [traceData]);
  const results = useMemo(
    () => recordsFrom(recordValue(trace, 'tool_results', 'toolResults')),
    [trace],
  );
  const rows = useMemo<TimelineRow[]>(() => {
    const resultByAction = new Map(results.map((result) => [actionId(result), result]));
    const includedResults = new Set<string>();
    const output: TimelineRow[] = events.map((event, eventIndex) => {
      const action = event.actionId || event.toolCallId || '';
      const isTool = event.stage === 'tool' || event.stage === 'execute';
      const result = isTool ? resultByAction.get(action) : undefined;
      if (result) includedResults.add(action);
      let modelTurn: number | undefined;
      if (event.stage === 'model') {
        const explicit = recordValue(event.details, 'model_turn', 'modelTurn');
        if (typeof explicit === 'number' && explicit > 0) {
          modelTurn = explicit;
        } else {
          modelTurn = events
            .slice(0, eventIndex + 1)
            .filter((candidate) => candidate.stage === 'model')
            .length;
        }
      }
      return {
        key: isTool ? `tool:${action || eventIndex}` : stageKey(event),
        event,
        result,
        kind: isTool ? 'tool' : 'stage',
        modelTurn,
      };
    });
    results.forEach((result, resultIndex) => {
      const key = actionId(result);
      if (key && includedResults.has(key)) return;
      output.push({ key: `tool:${key || `unmatched-${resultIndex}`}`, result, kind: 'tool' });
    });
    return output;
  }, [events, results]);
  const phases = useMemo(() => groupTimelinePhases(rows), [rows]);
  // A model.started event is an internal lifecycle marker, not user-facing
  // progress. During a live run, do not turn that marker into a separate
  // phase card before the model has emitted its readable plan. Tool/evidence/
  // approval phases remain visible, and terminal history keeps every phase.
  const visiblePhases = useMemo(
    () => messageActive ? phases.filter(phaseHasActiveDetails) : phases,
    [messageActive, phases],
  );
  const problemSummary = useMemo(() => {
    const problemEvent = [...events].reverse().find((event) => (
      event.status === 'failed'
      || event.status === 'blocked'
      || event.status === 'cancelled'
      || Boolean(event.errorCode)
    ));
    if (!problemEvent) return '';
    return problemEvent.summary.trim() || problemEvent.errorCode || '本轮执行未完成，请重试';
  }, [events]);
  // Tool-level negative outcomes are still valid terminal decisions (for
  // example, a user rejecting an approval).  Only a failed lifecycle event
  // makes the whole run incomplete; the individual tool row keeps its own
  // warning state and explanation.
  const hasProblem = problemSummary.length > 0;
  const eventDurationMs = useMemo(() => agentStageDurationMs(events), [events]);
  const streamDurationMs = typeof messageTiming?.totalStreamTime === 'number'
    && Number.isFinite(messageTiming.totalStreamTime)
    && messageTiming.totalStreamTime >= 0
    ? messageTiming.totalStreamTime
    : undefined;
  // A terminal trace placeholder has no local stream timer and assistant-ui
  // reports 0. Prefer the durable event interval when it contains real time.
  const durationMs = streamDurationMs !== undefined
    && (streamDurationMs > 0 || eventDurationMs === undefined)
    ? streamDurationMs
    : eventDurationMs;
  const durationLabel = durationMs == null ? null : formatElapsedDuration(durationMs);
  const compactLabel = hasProblem && !messageActive
    ? `执行未完成${durationLabel ? ` · 用时 ${durationLabel}` : ''}`
    : durationLabel ? `用时 ${durationLabel}` : '用时';
  const detailId = useId();
  const [expandedOverride, setExpandedOverride] = useState<boolean | null>(null);
  const expanded = messageActive || expandedOverride === true;
  const processVisible = visibleReasoningText(
    processText.trim() || (phases.length === 0 ? reasoningText : ''),
  );

  if (visiblePhases.length === 0 && !processVisible.trim()) return null;

  // A live fallback is still a normal stream.  It must not add the terminal
  // duration row or a second execution card above the message content.
  if (messageActive) {
    return (
      <section className="mb-3 min-w-0" aria-label="执行过程">
        {processVisible.trim() ? (
          <div className="mb-2">
            <AssistantMarkdown text={processVisible} evidence={trace} />
          </div>
        ) : null}
        {visiblePhases.length > 0 ? (
          <ol className="space-y-1">
            {visiblePhases.map((phase) => (
              <TimelinePhaseRow key={phase.key} phase={phase} />
            ))}
          </ol>
        ) : null}
      </section>
    );
  }

  if (presentation === 'inline') {
    return (
      <section className="mb-3 min-w-0" aria-label="执行过程">
        {processVisible.trim() ? (
          <div className="mb-2">
            <AssistantMarkdown text={processVisible} evidence={trace} />
          </div>
        ) : null}
        {visiblePhases.length > 0 ? (
          <ol className="space-y-1">
            {visiblePhases.flatMap((phase) => phase.rows.map((row) => row.kind === 'tool'
              ? <TimelineToolRow key={`${phase.key}:${row.key}`} row={row} />
              : <TimelineStageRow key={`${phase.key}:${row.key}`} row={row} />))}
          </ol>
        ) : null}
      </section>
    );
  }

  return (
    <section className="mb-3 min-w-0 overflow-hidden" aria-label="执行过程">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={detailId}
        aria-label={`${expanded ? '收起' : '展开'}${compactLabel}`}
        onClick={() => setExpandedOverride((value) => value === true ? false : true)}
        className="flex w-full min-w-0 items-center justify-between gap-3 border-b border-border/70 py-2 text-left text-sm text-muted-foreground transition-colors duration-300 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
      >
        <span className="min-w-0 truncate">{compactLabel}</span>
        <ChevronRightIcon
          className={cn(
            'size-4 shrink-0 transition-transform duration-300 ease-out',
            expanded && 'rotate-90',
          )}
          aria-hidden="true"
        />
      </button>
      {hasProblem && problemSummary ? (
        <div
          role="status"
          className="flex items-start gap-2 rounded-lg border border-amber-300/60 bg-amber-50/70 px-3 py-2 text-xs text-amber-900"
        >
          <CircleAlertIcon className="mt-0.5 size-3.5 shrink-0 text-amber-600" />
          <span className="min-w-0 whitespace-pre-wrap break-words">{problemSummary}</span>
        </div>
      ) : null}

      <div
        id={detailId}
        role="region"
        aria-label="执行过程详情"
        aria-hidden={!expanded}
        className="grid transition-[grid-template-rows] duration-300 ease-out"
        style={{ gridTemplateRows: expanded ? '1fr' : '0fr' }}
      >
        <div className="min-h-0 overflow-hidden">
          <div className={cn(
            'pt-2 transition-opacity duration-300 ease-out',
            expanded ? 'opacity-100' : 'opacity-0',
          )}>
            {processVisible.trim() ? (
              <div className={cn('mb-3', phases.length > 0 && 'border-b border-primary/10 pb-2')}>
                <AssistantMarkdown text={processVisible} evidence={trace} />
              </div>
            ) : null}
            {visiblePhases.length > 0 ? (
              <ol className="space-y-2">
                {visiblePhases.map((phase) => (
                  <TimelinePhaseRow key={phase.key} phase={phase} />
                ))}
              </ol>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
};

export const AgentStageIndicator: FC<{ event: AgentStageEventV2 }> = ({ event }) => {
  const problem = Boolean(event.errorCode)
    || event.status === 'failed'
    || event.status === 'blocked'
    || event.status === 'cancelled';
  return (
    <div className={cn(
      'mb-2 flex min-w-0 items-center gap-2 rounded-lg border px-3 py-2 text-xs',
      problem ? 'border-amber-300/50 bg-amber-50/70 text-amber-900' : 'border-primary/15 bg-primary/[0.035] text-muted-foreground',
    )} role="status">
      <StatusIcon
        status={event.status}
        problem={problem}
        className={cn('size-3.5 shrink-0', !problem && event.status === 'started' && 'animate-spin text-primary')}
      />
      <span className="shrink-0 font-medium text-foreground">{agentStageLabel(event.stage)}</span>
      <span className="min-w-0 whitespace-pre-wrap break-words">{event.summary}</span>
    </div>
  );
};
