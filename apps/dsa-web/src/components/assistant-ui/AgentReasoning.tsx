import type { FC } from 'react';
import { useId, useMemo, useState } from 'react';
import { useMessage, useMessageTiming } from '@assistant-ui/react';
import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import {
  BookOpenIcon,
  CircleAlertIcon,
  ChevronRightIcon,
  Loader2Icon,
} from 'lucide-react';
import {
  agentStageDurationMs,
  agentStageEvents,
  agentStageLabel,
  reconcileTerminalStageEvents,
  type AgentStageEventV2,
} from '../../utils/agentStage';
import { cn } from '../../utils/cn';
import { formatElapsedDuration } from '../../utils/format';
import { AssistantMarkdown } from './AssistantMarkdownText';
import {
  actionId,
  argumentSummary,
  groupTimelinePhases,
  isRecord,
  phaseHasActiveDetails,
  recordValue,
  recordsFrom,
  stageKey,
  toolDetails,
  toolPartLabel,
  type TimelineRow,
} from './AgentReasoningUtils';

import {
  StatusIcon,
  TimelineDetailLines,
  TimelinePhaseRow,
  TimelineStageRow,
  TimelineToolRow,
} from './AgentReasoningTimeline';

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
  /** Native assistant parts already render tool calls in chronological order.
   * In that mode keep only the durable control stages (evidence/reflection/
   * approval) so those checks remain visible without duplicating tool rows. */
  stageOnly?: boolean;
}> = ({
  reasoningText = '',
  processText = '',
  presentation = 'disclosure',
  stageOnly = false,
}) => {
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
    return stageOnly
      ? output.filter((row) => row.kind === 'stage'
        && row.event
        && !['model', 'publish'].includes(row.event.stage))
      : output;
  }, [events, results, stageOnly]);
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
      !['tool', 'execute', 'approval'].includes(event.stage)
      && (
      event.status === 'failed'
      || event.status === 'blocked'
      || event.status === 'cancelled'
      || Boolean(event.errorCode)
      )
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
      <section className="mb-3 min-w-0" aria-label={stageOnly ? '校验与复核' : '执行过程'}>
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
