import type { ErrorInfo, FC, ReactNode } from 'react';
import { Component, useId, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import {
  AuiIf,
  ThreadPrimitive,
  MessagePrimitive,
  ActionBarPrimitive,
  useMessage,
  useMessageTiming,
  useScrollLock,
  useAuiState,
} from '@assistant-ui/react';
import type { TextMessagePartProps } from '@assistant-ui/react';
import {
  BookOpenIcon,
  CircleAlertIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  Loader2Icon,
  CopyIcon,
  RefreshCwIcon,
  DownloadIcon,
  Volume2Icon,
  SquareIcon as StopIcon,
} from 'lucide-react';
import { AssistantMarkdown, AssistantMarkdownText } from './AssistantMarkdownText';
import {
  StructuredAnswerReferences,
} from './StructuredAnswerReferences';
import {
  stripStructuredAnswerReferenceFallbacks,
  structuredAnswerFromTrace,
} from './StructuredAnswerReferencesUtils';
import { splitAssistantText } from '../../utils/assistantTextSplit';
import {
  assistantAnswerTextFromContent as answerTextFromContent,
  assistantDisplayKindOf as displayKindOf,
  assistantPostToolBodyText as firstPostToolProgressText,
  hasAssistantDisplayMetadata as hasDisplayMetadata,
} from '../../utils/assistantAnswer';
import { cn } from '../../utils/cn';
import {
  agentStageDurationMs,
  agentStageEvents,
  reconcileTerminalStageEvents,
} from '../../utils/agentStage';
import { formatElapsedDuration } from '../../utils/format';
import { AgentExecutionTimeline, AgentToolCallPart } from './AgentReasoning';
import { isRecord } from './AgentReasoningUtils';
import { Composer } from './ThreadComposer';
import { EmptyState } from './ThreadEmptyState';
import { UserMessage } from './ThreadUserMessage';

/* ── Thread (root) ───────────────────────────────────────────────────── */

const Thread: FC<{ onUserCancel?: () => void; onDeleteUserTurn?: (messageId: string) => void }> = ({
  onUserCancel,
  onDeleteUserTurn,
}) => {
  const isRunning = useAuiState((state) => state.thread.isRunning);

  return (
    <ThreadPrimitive.Root className="flex h-full min-h-0 flex-col overflow-hidden">
      <ThreadPrimitive.Viewport
        autoScroll={isRunning}
        data-chat-thread-viewport="true"
        style={{ overflowAnchor: 'none' }}
        className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto bg-[linear-gradient(180deg,hsl(var(--background)),hsl(var(--background)))] px-3 pb-4 pt-2 sm:gap-4 sm:px-4 sm:pb-5 lg:px-6 lg:pt-2"
      >
        <AuiIf condition={(s) => s.thread.isEmpty}>
          <EmptyState />
        </AuiIf>

        <AuiIf condition={(s) => !s.thread.isEmpty}>
          <div className="mx-auto w-full max-w-3xl">
            <ThreadPrimitive.Messages
              components={{
                UserMessage: () => <UserMessage onDeleteTurn={onDeleteUserTurn} />,
                AssistantMessage: GuardedAssistantMessage,
              }}
            />
          </div>
        </AuiIf>

        <AuiIf condition={(s) => !s.thread.isEmpty}>
          <ThreadPrimitive.ViewportFooter className="sticky bottom-0 pt-2">
            <div className="mx-auto w-full max-w-3xl">
              <ThreadPrimitive.ScrollToBottom
                className={cn(
                  'mx-auto mb-2 flex size-8 items-center justify-center',
                  'rounded-full border border-border bg-card text-muted-foreground',
                  'shadow-sm transition hover:text-foreground',
                )}
              >
                <ChevronDownIcon className="size-4" />
              </ThreadPrimitive.ScrollToBottom>
            </div>
          </ThreadPrimitive.ViewportFooter>
        </AuiIf>
      </ThreadPrimitive.Viewport>

      <Composer onUserCancel={onUserCancel} />
    </ThreadPrimitive.Root>
  );
};

/* ── Assistant Message ───────────────────────────────────────────────── */

interface AssistantMessageBoundaryProps {
  children: ReactNode;
  resetKey: string;
}

interface AssistantMessageBoundaryState {
  hasError: boolean;
}

class AssistantMessageBoundary extends Component<
  AssistantMessageBoundaryProps,
  AssistantMessageBoundaryState
> {
  override state: AssistantMessageBoundaryState = { hasError: false };

  static getDerivedStateFromError(): AssistantMessageBoundaryState {
    return { hasError: true };
  }

  override componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('[Chat] assistant message rendering failed; preserving the rest of the conversation.', error, errorInfo);
  }

  override componentDidUpdate(previousProps: AssistantMessageBoundaryProps) {
    if (previousProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false });
    }
  }

  override render() {
    if (this.state.hasError) {
      return (
        <div
          className="mb-2 rounded-xl border border-amber-300/60 bg-amber-50/70 px-3 py-2 text-xs text-amber-900"
          role="status"
        >
          本条执行详情暂时无法显示；运行记录仍已保留，可刷新后重试查看。
        </div>
      );
    }
    return this.props.children;
  }
}

const InlineMessagePartGroup: FC<{ children?: ReactNode }> = ({ children }) => <>{children}</>;

const DISCLOSURE_ANIMATION_DURATION_MS = 300;

type NativeDisclosureProps = {
  children?: ReactNode;
  open: boolean;
  onToggle: () => void;
  detailId: string;
  ariaLabel: string;
  renderTrigger?: (toggle: () => void) => ReactNode;
};

/**
 * Keep the details in the message's normal flow so the answer moves through
 * the same local layout as the details collapse. Motion owns the height
 * interpolation, while the viewport's scroll lock and disabled scroll
 * anchoring prevent the thread from being treated as a page transition.
 */
const NativeDisclosure: FC<NativeDisclosureProps> = ({
  children,
  open,
  onToggle,
  detailId,
  ariaLabel,
  renderTrigger,
}) => {
  const disclosureRef = useRef<HTMLDivElement | null>(null);
  const lockScroll = useScrollLock(disclosureRef, DISCLOSURE_ANIMATION_DURATION_MS);

  const toggle = () => {
    lockScroll();
    onToggle();
  };

  return (
    <div ref={disclosureRef} className="min-w-0">
      {renderTrigger?.(toggle)}
      <AnimatePresence initial={false}>
        {open ? (
          <motion.div
            id={detailId}
            role="region"
            aria-label={ariaLabel}
            aria-hidden={false}
            initial={{ height: 0 }}
            animate={{ height: 'auto' }}
            exit={{ height: 0 }}
            transition={{ duration: DISCLOSURE_ANIMATION_DURATION_MS / 1000, ease: 'easeOut' }}
            className="min-w-0 overflow-hidden"
          >
            <div className="min-w-0">{children}</div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
};

/**
 * assistant-ui already groups adjacent tool-call parts from the same streamed
 * message. Keep that grouping boundary and make the group the single
 * disclosure surface for the execution stage. Individual tools still use
 * AgentToolCallPart's own disclosure for arguments and results.
 */
const NativeToolGroup: FC<{
  children?: ReactNode;
  startIndex: number;
  endIndex: number;
}> = ({ children, startIndex, endIndex }) => {
  const activeCount = useAuiState((state) => state.message.parts
    .slice(startIndex, endIndex + 1)
    .filter((part) => (
      part.type === 'tool-call'
      && (part.status.type === 'running' || part.status.type === 'requires-action')
    )).length);
  const failedCount = useAuiState((state) => state.message.parts
    .slice(startIndex, endIndex + 1)
    .filter((part) => {
      if (!isRecord(part) || part.type !== 'tool-call') return false;
      const status = isRecord(part.status) ? part.status : {};
      const result = isRecord(part.result) ? part.result : undefined;
      return part.isError === true
        || result?.success === false
        || (status.type === 'incomplete' && status.reason === 'error');
    }).length);
  const [expanded, setExpanded] = useState(false);
  const detailId = useId();
  const toolCount = Math.max(1, endIndex - startIndex + 1);
  const completedCount = Math.max(0, toolCount - activeCount - failedCount);
  const label = [
    activeCount > 0 ? `正在执行 ${activeCount} 个工具` : '',
    completedCount > 0 ? `已完成 ${completedCount} 个工具` : '',
    failedCount > 0 ? `失败 ${failedCount} 个工具` : '',
  ].filter(Boolean).join('，');
  const active = activeCount > 0;

  return (
    <div className="relative min-w-0">
      <NativeDisclosure
        open={expanded}
        onToggle={() => setExpanded((value) => !value)}
        detailId={detailId}
        ariaLabel="阶段工具调用详情"
        renderTrigger={(toggle) => (
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={detailId}
            aria-label={`${expanded ? '收起' : '展开'}阶段工具调用`}
            onClick={toggle}
            className="flex w-full min-w-0 items-center gap-2 py-2 text-left text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
          >
            {active ? (
              <Loader2Icon className="size-4 shrink-0 animate-spin text-primary" aria-hidden="true" />
            ) : failedCount > 0 ? (
              <CircleAlertIcon className="size-4 shrink-0 text-amber-600" aria-hidden="true" />
            ) : (
              <BookOpenIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            )}
            <span className="min-w-0 flex-1 truncate">{label} {toolCount} 个工具</span>
            <ChevronRightIcon
              className={cn(
                'size-4 shrink-0 transition-transform duration-300 ease-out',
                expanded && 'rotate-90',
              )}
              aria-hidden="true"
            />
          </button>
        )}
      >
        <div className="pl-2">{children}</div>
      </NativeDisclosure>
    </div>
  );
};

const NativeTextPart: FC<TextMessagePartProps> = (part) => (
  <NativeTextPartContent {...part} />
);

const NativeTextPartContent: FC<TextMessagePartProps> = (part) => {
  const messageStatus = useMessage((state) => state.status?.type);
  const active = messageStatus === 'running' || messageStatus === 'requires-action';
  const answerText = useMessage((state) => answerTextFromContent(state.content));
  const postToolBody = useMessage((state) => firstPostToolProgressText(state.content));
  const isTerminalFallbackAnswer = !active
    && displayKindOf(part) === null
    && part.text.trim().length > 0
    && part.text.trim() === answerText.trim();
  const isTerminalPostToolBody = !active
    && postToolBody.trim().length > 0
    && part.text.trim() === postToolBody.trim();

  return displayKindOf(part) === 'answer' || isTerminalFallbackAnswer || isTerminalPostToolBody
    ? null
    : <AssistantMarkdownText {...part} />;
};

const NativeExecutionDisclosure: FC<{ children?: ReactNode }> = ({ children }) => {
  const messageStatus = useMessage((state) => state.status?.type);
  const active = messageStatus === 'running' || messageStatus === 'requires-action';
  const messageTiming = useMessageTiming();
  const stageData = useMessage((state) => state.metadata?.unstable_data);
  const events = useMemo(
    () => reconcileTerminalStageEvents(agentStageEvents(stageData)),
    [stageData],
  );
  const eventDurationMs = useMemo(() => agentStageDurationMs(events), [events]);
  const streamDurationMs = typeof messageTiming?.totalStreamTime === 'number'
    && Number.isFinite(messageTiming.totalStreamTime)
    && messageTiming.totalStreamTime >= 0
    ? messageTiming.totalStreamTime
    : undefined;
  const durationMs = streamDurationMs ?? eventDurationMs;
  const durationLabel = durationMs == null ? '—' : formatElapsedDuration(durationMs);
  const compactLabel = active ? '执行中' : `用时 ${durationLabel}`;
  const detailId = useId();
  const [expandedOverride, setExpandedOverride] = useState<boolean | null>(null);
  const expanded = active || expandedOverride === true;

  return (
    <section className="relative mb-3 min-w-0" aria-label="执行过程">
      <NativeDisclosure
        open={expanded}
        onToggle={() => setExpandedOverride((value) => value === true ? false : true)}
        detailId={detailId}
        ariaLabel="执行过程详情"
        renderTrigger={!active ? (toggle) => (
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={detailId}
            aria-label={`${expanded ? '收起' : '展开'}${compactLabel}`}
            onClick={toggle}
            className="flex w-full min-w-0 items-center justify-between gap-3 py-2 text-left text-sm text-muted-foreground transition-colors duration-300 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
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
        ) : undefined}
      >
        <div className="pt-0">
          {children}
          <AgentExecutionTimeline presentation="inline" stageOnly />
        </div>
      </NativeDisclosure>
    </section>
  );
};

const NativeAssistantParts: FC = () => {
  return <MessagePrimitive.Parts
    unstable_showEmptyOnNonTextEnd={false}
    components={{
      Text: NativeTextPart,
      Reasoning: () => null,
      tools: { Fallback: AgentToolCallPart },
      ToolGroup: NativeToolGroup,
      ReasoningGroup: InlineMessagePartGroup,
    }}
  />;
};

const AssistantMessage: FC = () => {
  const messageStatus = useMessage((s) => s.status?.type);
  const isActive = messageStatus === 'running' || messageStatus === 'requires-action';
  const reasoningText = useMessage((s) =>
    s.content
      .map((part) => (part.type === 'reasoning' ? part.text : ''))
      .filter(Boolean)
      .join('\n'),
  );
  // Keep each external-store selector primitive. Returning a fresh object
  // here makes useSyncExternalStore believe the snapshot changed forever.
  // Native assistant-stream parts remain the source of truth for chronology.
  // A terminal replay is marked with provider metadata, so a no-tool run can
  // use the same ordered renderer as a tool run without inferring boundaries
  // from the distance between text parts.
  const answerText = useMessage((s) => answerTextFromContent(s.content));
  const evidenceTrace = useMessage((s) => (
    s.metadata?.custom?.agent_execution_trace
    ?? s.metadata?.custom?.agentExecutionTrace
  ));
  const structuredAnswer = structuredAnswerFromTrace(evidenceTrace);
  const displayAnswerText = stripStructuredAnswerReferenceFallbacks(answerText, structuredAnswer);
  const hasOrderedPart = useMessage((s) => s.content.some((part) => {
    if (part.type === 'tool-call') return true;
    // The default assistant-ui reasoning renderer is intentionally hidden;
    // private reasoning alone must not suppress the pending/stage fallback.
    if (part.type === 'text') return part.text.trim().length > 0;
    return false;
  }));
  const hasNativeDisplayPart = useMessage((s) => s.content.some((part) => (
    part.type === 'tool-call'
    || ((part.type === 'text' || part.type === 'reasoning') && hasDisplayMetadata(part))
  )));
  const hasNativeAnswerPart = useMessage((s) => s.content.some((part) => (
    part.type === 'text' && displayKindOf(part) === 'answer'
  )));
  const hasNativeTerminalAnswer = !isActive && hasNativeDisplayPart && answerText.trim().length > 0;
  const hasNativeProcessPart = useMessage((s) => s.content.some((part) => {
    if (part.type === 'tool-call') return true;
    if (part.type === 'text') {
      return displayKindOf(part) !== 'answer' && part.text.trim().length > 0;
    }
    return false;
  }));
  const hasVisibleContent = useMessage((s) =>
    s.content.some((part) => {
      if (part.type === 'text') {
        return splitAssistantText(part.text).content.trim().length > 0;
      }
      return false;
    }),
  );
  const hasActiveExecutionDetail = useMessage((s) => agentStageEvents(s.metadata?.unstable_data).some((event) => (
    event.stage !== 'model' && event.stage !== 'publish'
  )));
  const hasExecutionRecord = useMessage((s) => {
    if (Array.isArray(s.metadata?.unstable_data) && s.metadata.unstable_data.length > 0) {
      return true;
    }
    const trace = s.metadata?.custom?.agent_execution_trace
      ?? s.metadata?.custom?.agentExecutionTrace;
    return Boolean(trace && typeof trace === 'object');
  });
  if (!isActive && !hasVisibleContent && !hasExecutionRecord) {
    return null;
  }
  return (
    <MessagePrimitive.Root className="group/message mb-1.5 flex w-full min-w-0 items-start justify-start">
      <div className="relative min-w-0 flex-1 pb-5">
        <div className="w-full min-w-0 overflow-hidden text-sm text-foreground">
          {isActive || hasNativeDisplayPart ? (
            <>
              {hasNativeProcessPart ? (
                <NativeExecutionDisclosure>
                  <NativeAssistantParts />
                </NativeExecutionDisclosure>
              ) : null}
              {hasNativeTerminalAnswer || hasNativeAnswerPart ? (
                <AssistantMarkdown text={displayAnswerText} evidence={evidenceTrace} />
              ) : null}
              <StructuredAnswerReferences answer={structuredAnswer} renderedText={displayAnswerText} />
              {isActive && !hasOrderedPart && hasExecutionRecord && hasActiveExecutionDetail ? (
                <AgentExecutionTimeline reasoningText={reasoningText} />
              ) : null}
              {isActive && !hasOrderedPart && !hasNativeProcessPart && !hasVisibleContent && !hasActiveExecutionDetail ? (
                <AssistantPendingIndicator />
              ) : null}
            </>
          ) : (
            <>
              <AgentExecutionTimeline reasoningText={reasoningText} />
              <AssistantMarkdown text={displayAnswerText} evidence={evidenceTrace} />
              <StructuredAnswerReferences answer={structuredAnswer} renderedText={displayAnswerText} />
            </>
          )}
        </div>
        <div className="absolute bottom-0 left-0 flex h-5 items-center gap-1">
          <AssistantActionBar />
        </div>
      </div>
    </MessagePrimitive.Root>
  );
};

const GuardedAssistantMessage: FC = () => {
  const resetKey = useMessage((s) => {
    const stageCount = Array.isArray(s.metadata?.unstable_data) ? s.metadata.unstable_data.length : 0;
    return `${s.id}:${s.status?.type || 'idle'}:${s.content.length}:${stageCount}`;
  });
  return (
    <AssistantMessageBoundary resetKey={resetKey}>
      <AssistantMessage />
    </AssistantMessageBoundary>
  );
};

/* ── Assistant Action Bar (Copy / Reload) ────────────────────────────── */

const AssistantActionBar: FC = () => (
  <ActionBarPrimitive.Root
    hideWhenRunning
    autohide="not-last"
    className="flex h-5 items-center gap-0.5 opacity-0 transition-opacity group-hover/message:opacity-100 data-[copied]:opacity-100"
  >
    <ActionBarPrimitive.Copy
      copiedDuration={1500}
      className="flex size-5 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground data-[copied]:text-emerald-500"
      title="复制"
    >
      <CopyIcon className="size-3" />
    </ActionBarPrimitive.Copy>
    {/* 朗读回答(TTS):需在 runtime adapters 配 speech 合成器(见 ChatHomePage)。
        未配置或无文本时 Speak 自动隐藏;朗读中(s.message.speech 存在)显示 Stop。 */}
    <ActionBarPrimitive.Speak
      className="flex size-5 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-30"
      title="朗读"
    >
      <Volume2Icon className="size-3" />
    </ActionBarPrimitive.Speak>
    <AuiIf condition={(s) => s.message.speech != null}>
      <ActionBarPrimitive.StopSpeaking
        className="flex size-5 items-center justify-center rounded text-primary transition hover:bg-primary/10"
        title="停止朗读"
      >
        <StopIcon className="size-3" />
      </ActionBarPrimitive.StopSpeaking>
    </AuiIf>
    {/* 导出回答为 Markdown 文件(纯前端,无后端)。filename 带对话上下文更友好。 */}
    <ActionBarPrimitive.ExportMarkdown
      className="flex size-5 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-30"
      title="导出 Markdown"
    >
      <DownloadIcon className="size-3" />
    </ActionBarPrimitive.ExportMarkdown>
    <ActionBarPrimitive.Reload
      className="flex size-5 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-30"
      title="重新生成"
    >
      <RefreshCwIcon className="size-3" />
    </ActionBarPrimitive.Reload>
  </ActionBarPrimitive.Root>
);

const AssistantPendingIndicator: FC = () => (
  <div
    className="flex min-h-8 items-center gap-2 text-sm text-muted-foreground"
    role="status"
    aria-live="polite"
  >
    <Loader2Icon className="size-4 shrink-0 animate-spin text-primary/80 motion-reduce:animate-none" aria-hidden="true" />
    <span>正在思考</span>
  </div>
);

export default Thread;
