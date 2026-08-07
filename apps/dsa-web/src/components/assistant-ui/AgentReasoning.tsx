import type { FC } from 'react';
import { useMemo, useState } from 'react';
import { useMessage } from '@assistant-ui/react';
import {
  BrainCircuitIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  CircleAlertIcon,
  Loader2Icon,
} from 'lucide-react';
import {
  agentStageLabel,
  latestAgentStageEvent,
  reasoningStatusLabel,
  type AgentStageEventV2,
} from '../../utils/agentStage';
import { cn } from '../../utils/cn';

const MAX_VISIBLE_REASONING_CHARS = 60_000;

export const AgentStageIndicator: FC<{ event: AgentStageEventV2 }> = ({ event }) => {
  const isProblem = event.status === 'failed'
    || event.status === 'blocked'
    || event.status === 'cancelled';
  const isDone = event.status === 'succeeded' || event.status === 'completed';
  const Icon = isProblem ? CircleAlertIcon : isDone ? CheckCircle2Icon : Loader2Icon;
  const stageLabel = event.status === 'failed'
    ? '执行失败'
    : event.status === 'blocked'
      ? '执行已阻断'
      : event.status === 'cancelled'
        ? '执行已取消'
        : agentStageLabel(event.stage);
  return (
    <div
      className={cn(
        'mb-2.5 flex min-w-0 items-center gap-2 rounded-lg border px-3 py-2 text-xs',
        isProblem
          ? 'border-amber-300/50 bg-amber-50/70 text-amber-800'
          : 'border-primary/15 bg-primary/[0.035] text-muted-foreground',
      )}
      role="status"
      aria-live="polite"
    >
      <Icon className={cn('size-3.5 shrink-0', !isProblem && !isDone && 'animate-spin text-primary')} />
      <span className="shrink-0 font-medium text-foreground">{stageLabel}</span>
      {event.summary ? <span className="min-w-0 truncate">{event.summary}</span> : null}
    </div>
  );
};

export const AssistantReasoning: FC<{ text: string }> = ({ text }) => {
  const [expanded, setExpanded] = useState(true);
  const messageRunning = useMessage((state) => state.status?.type === 'running');
  const stageEvents = useMessage((state) => state.metadata?.unstable_data);
  const latestStage = useMemo(
    () => latestAgentStageEvent(stageEvents),
    [stageEvents],
  );
  const failed = latestStage?.status === 'failed' || latestStage?.status === 'blocked';
  const cancelled = latestStage?.status === 'cancelled';
  const completed = latestStage?.status === 'completed'
    || (latestStage?.stage === 'completed' && latestStage.status === 'succeeded');
  const statusLabel = reasoningStatusLabel(messageRunning, latestStage);
  const visibleText = useMemo(() => {
    if (text.length <= MAX_VISIBLE_REASONING_CHARS) return text;
    const omitted = text.length - MAX_VISIBLE_REASONING_CHARS;
    return [
      `[较早的实时过程已折叠 ${omitted} 字，以下显示最近过程]`,
      text.slice(-MAX_VISIBLE_REASONING_CHARS),
    ].join('\n');
  }, [text]);

  if (!text.trim()) return null;

  return (
    <div className="mb-3 w-full min-w-0 rounded-xl border border-primary/15 bg-primary/[0.035]">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left"
        aria-expanded={expanded}
      >
        <span className="flex min-w-0 items-center gap-2 text-sm font-medium text-foreground">
          <BrainCircuitIcon className="size-4 shrink-0 text-primary" />
          <span>实时分析过程</span>
        </span>
        <span className="flex items-center gap-2 text-xs text-muted-foreground">
          {messageRunning ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : failed || cancelled || !completed ? (
            <CircleAlertIcon className="size-3.5 text-amber-500" />
          ) : (
            <CheckCircle2Icon className="size-3.5 text-emerald-500" />
          )}
          {statusLabel}
          <ChevronDownIcon className={`size-4 transition-transform ${expanded ? 'rotate-180' : ''}`} />
        </span>
      </button>

      {expanded && (
        <div className="border-t border-primary/10 px-3 py-2.5">
          <div className="whitespace-pre-wrap break-words text-xs leading-6 text-muted-foreground">
            {visibleText}
          </div>
        </div>
      )}
    </div>
  );
};
