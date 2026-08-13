import type { FC } from 'react';
import { useMemo, useState } from 'react';
import { useMessage } from '@assistant-ui/react';
import {
  CheckCircle2Icon,
  ChevronDownIcon,
  CircleAlertIcon,
  Loader2Icon,
} from 'lucide-react';
import {
  agentStageEvents,
  agentStageLabel,
  reasoningStatusLabel,
  reconcileTerminalStageEvents,
  type AgentStageEvent,
  type AgentStageEventV2,
} from '../../utils/agentStage';
import { cn } from '../../utils/cn';

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
    if (operations.length > 0) {
      return operations
        .map((operation, index) => {
          const name = toolName(operation) || '原子操作';
          const argumentsText = argumentSummary(recordValue(operation, 'arguments'));
          const argumentKeys = recordValue(operation, 'argument_keys', 'argumentKeys');
          const keys = Array.isArray(argumentKeys)
            ? argumentKeys.map((value) => text(value, 96)).filter(Boolean)
            : [];
          const request = argumentsText || (keys.length > 0 ? `参数字段：${keys.join('、')}` : '无参数');
          return { key: `operation-${index}`, text: `操作 ${index + 1}：${name} · ${request}` };
        });
    }
    // The candidate answer is already represented by the stage summary and
    // will be published directly below the timeline. Rendering its body here
    // duplicates the answer once per evidence-repair round.
    return [];
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

export const AgentExecutionTimeline: FC = () => {
  const messageRunning = useMessage((state) => state.status?.type === 'running');
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
      const action = event.actionId || '';
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
      return { key: stageKey(event), event, result, kind: isTool ? 'tool' : 'stage', modelTurn };
    });
    for (const result of results) {
      const key = actionId(result);
      if (!key || includedResults.has(key)) continue;
      output.push({ key: `tool:${key}`, result, kind: 'tool' });
    }
    return output;
  }, [events, results]);
  const [expanded, setExpanded] = useState(true);

  if (rows.length === 0) return null;
  const latest = events.at(-1) || null;
  const terminalProblem = Boolean(latest?.errorCode)
    || latest?.status === 'failed'
    || latest?.status === 'blocked'
    || latest?.status === 'cancelled';
  const done = !messageRunning && Boolean(latest) && !terminalProblem;
  const headerStatus: AgentStageEvent['status'] = terminalProblem
    ? 'failed'
    : done ? 'completed' : 'started';

  return (
    <section className="mb-3 overflow-hidden rounded-xl border border-primary/15 bg-primary/[0.035]" aria-label="执行过程">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left"
        aria-expanded={expanded}
      >
        <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5">
          <StatusIcon
            status={headerStatus}
            problem={terminalProblem}
            className={cn('size-4 shrink-0', !done && !terminalProblem && 'animate-spin text-primary')}
          />
          <span className="text-sm font-medium text-foreground">执行过程</span>
          <span className="text-xs text-muted-foreground">{rows.length} 条实际记录</span>
          {latest?.summary && !expanded ? (
            <span className="min-w-0 break-words text-xs text-muted-foreground">· {latest.summary}</span>
          ) : null}
        </span>
        <ChevronDownIcon className={cn('size-4 shrink-0 text-muted-foreground transition-transform', expanded && 'rotate-180')} />
      </button>

      {expanded ? (
        <div className="border-t border-primary/10 px-3 py-2">
          <ol className="space-y-1">
            {rows.map((row) => {
              const event = row.event;
              const status = row.kind === 'tool' ? toolStatus(row.result, event) : (event?.status || 'started');
              const problem = status === 'failed' || status === 'blocked' || status === 'cancelled'
                || Boolean(row.kind === 'stage' && event?.errorCode);
              const label = row.kind === 'tool'
                ? (toolName(row.result) || text(recordValue(event?.details, 'tool_name', 'toolName'), 120) || '原子工具')
                : `${agentStageLabel(event?.stage || '')}${row.modelTurn ? ` · 第 ${row.modelTurn} 轮` : ''}`;
              const summary = row.kind === 'tool'
                ? toolSummary(row.result, event)
                : (event?.summary || '正在处理');
              const detailLines = row.kind === 'tool' ? toolDetails(row.result, event) : stageDetails(event!);
              return (
                <li key={row.key} className="flex min-w-0 items-start gap-2 py-1.5 text-xs">
                  <StatusIcon status={status} problem={problem} className={cn(
                    'mt-0.5 size-3.5 shrink-0',
                    problem ? 'text-amber-600' : status === 'completed' || status === 'succeeded' ? 'text-emerald-600' : 'animate-spin text-primary',
                  )} />
                  <div className="min-w-0 flex-1 leading-5">
                    <span className="font-medium text-foreground">{label}</span>
                    <span className="ml-2 text-muted-foreground">{statusText(status, problem)}</span>
                    <span className="ml-2 whitespace-pre-wrap break-words text-muted-foreground">{summary}</span>
                    {detailLines.length > 0 ? (
                      <div className="mt-0.5 space-y-0.5 text-[11px] leading-5 text-muted-foreground/85">
                        {detailLines.map((detail) => (
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
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}
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

export const AssistantReasoning: FC<{ text: string }> = ({ text: rawText }) => {
  const [expanded, setExpanded] = useState(false);
  const messageRunning = useMessage((state) => state.status?.type === 'running');
  const stageData = useMessage((state) => state.metadata?.unstable_data);
  const latest = useMemo(
    () => reconcileTerminalStageEvents(agentStageEvents(stageData)).at(-1) ?? null,
    [stageData],
  );
  const text = rawText.length > 12_000 ? `${rawText.slice(-12_000)}\n[较早过程已折叠]` : rawText;
  if (!text.trim()) return null;
  return (
    <div className="mb-3 rounded-lg border border-border/70 bg-muted/25 px-3 py-2">
      <button type="button" onClick={() => setExpanded((value) => !value)} className="flex w-full items-center justify-between text-xs text-muted-foreground">
        <span>运行日志 · {reasoningStatusLabel(messageRunning, latest)}</span>
        <ChevronDownIcon className={cn('size-3.5 transition-transform', expanded && 'rotate-180')} />
      </button>
      {expanded ? <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-muted-foreground">{text}</pre> : null}
    </div>
  );
};
