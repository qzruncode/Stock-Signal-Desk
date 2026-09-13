import type { FC } from 'react';
import { useId, useState } from 'react';
import {
  CheckCircle2Icon,
  CircleAlertIcon,
  ChevronRightIcon,
  Loader2Icon,
} from 'lucide-react';
import {
  agentStageLabel,
  type AgentStageEvent,
} from '../../utils/agentStage';
import { cn } from '../../utils/cn';
import { AssistantMarkdown } from './AssistantMarkdownText';
import {
  errorCode,
  phaseHeadline,
  phaseProblem,
  phaseStatus,
  recordValue,
  stageDetails,
  statusText,
  text,
  toolDetails,
  toolOutcomeCounts,
  toolName,
  toolStatus,
  toolSummary,
  type DetailLine,
  type TimelinePhase,
  type TimelineRow,
} from './AgentReasoningUtils';

export const StatusIcon: FC<{
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

export const TimelineDetailLines: FC<{ lines: DetailLine[] }> = ({ lines }) => {
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

export const TimelineStageRow: FC<{ row: TimelineRow }> = ({ row }) => {
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

export const TimelineToolRow: FC<{ row: TimelineRow }> = ({ row }) => {
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

export const TimelinePhaseRow: FC<{ phase: TimelinePhase }> = ({ phase }) => {
  const [expandedOverride, setExpandedOverride] = useState(false);
  const detailId = useId();
  const expanded = expandedOverride;
  const status = phaseStatus(phase);
  const problem = phaseProblem(phase);
  const toolRows = phase.rows.filter((row) => row.kind === 'tool');
  const phaseNumber = phase.modelTurn || phase.index;
  const phaseLabel = `第 ${phaseNumber} 阶段`;
  const headline = phaseHeadline(phase);
  const outcomes = toolOutcomeCounts(toolRows);
  const phaseToolSummary = [
    outcomes.running > 0 ? `正在执行 ${outcomes.running} 个工具` : '',
    outcomes.completed > 0 ? `已完成 ${outcomes.completed} 个工具` : '',
    outcomes.failed > 0 ? `失败 ${outcomes.failed} 个工具` : '',
  ].filter(Boolean).join('，');
  const phaseSummary = toolRows.length > 0
    ? phaseToolSummary
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
