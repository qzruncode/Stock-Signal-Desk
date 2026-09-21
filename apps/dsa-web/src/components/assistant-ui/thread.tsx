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
import type { DataMessagePartProps, TextMessagePartProps } from '@assistant-ui/react';
import {
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
  ChartReference,
  StructuredAnswerReferences,
} from './StructuredAnswerReferences';
import {
  normalizeChartReference,
  stripNativeAnswerReferenceFallbacks,
  stripStructuredAnswerReferenceFallbacks,
  structuredAnswerFromTrace,
} from './StructuredAnswerReferencesUtils';
import { splitAssistantText } from '../../utils/assistantTextSplit';
import {
  assistantAnswerTextFromContent as answerTextFromContent,
  assistantDisplayKindOf as displayKindOf,
  assistantPublishedAnswerTextFromContent as publishedAnswerTextFromContent,
  hasAssistantDisplayMetadata as hasDisplayMetadata,
} from '../../utils/assistantAnswer';
import { cn } from '../../utils/cn';
import {
  agentStageDurationMs,
  agentStageEvents,
  reconcileTerminalStageEvents,
} from '../../utils/agentStage';
import { formatElapsedDuration } from '../../utils/format';
import { AgentExecutionTimeline, AgentStageIndicator, AgentToolCallPart } from './AgentReasoning';
import { Composer } from './ThreadComposer';
import { EmptyState } from './ThreadEmptyState';
import { UserMessage } from './ThreadUserMessage';
import { TeamCollaborationView } from './TeamBoard';
import { isRecord } from './AgentReasoningUtils';
import type { AgentProductMode } from '../../utils/agentMode';

/* ── Thread (root) ───────────────────────────────────────────────────── */

const Thread: FC<{
  onUserCancel?: () => void;
  onDeleteUserTurn?: (messageId: string) => void;
  agentMode: AgentProductMode;
  onAgentModeChange: (mode: AgentProductMode) => void;
}> = ({
  onUserCancel,
  onDeleteUserTurn,
  agentMode,
  onAgentModeChange,
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
                AssistantMessage: () => <GuardedAssistantMessage productMode={agentMode} />,
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

      <Composer
        onUserCancel={onUserCancel}
        agentMode={agentMode}
        onAgentModeChange={onAgentModeChange}
      />
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
 * Keep consecutive tool parts as standalone children. Parallel workers may
 * legitimately produce adjacent calls, but wrapping the whole range in one
 * disclosure makes the chat look as if all tools happened at the end.
 */
const NativeToolGroup: FC<{
  children?: ReactNode;
  startIndex: number;
  endIndex: number;
}> = ({ children }) => <>{children}</>;

const NativeTextPart: FC<TextMessagePartProps> = (part) => (
  <NativeTextPartContent {...part} />
);

const NativeTextPartContent: FC<TextMessagePartProps> = (part) => {
  const messageStatus = useMessage((state) => state.status?.type);
  const active = messageStatus === 'running' || messageStatus === 'requires-action';
  const executionTrace = useMessage((state) => (
    state.metadata?.custom?.agent_execution_trace
      ?? state.metadata?.custom?.agentExecutionTrace
  ));
  const structuredAnswer = structuredAnswerFromTrace(executionTrace);
  const text = displayKindOf(part) === 'answer'
    ? stripNativeAnswerReferenceFallbacks(
        stripStructuredAnswerReferenceFallbacks(part.text, structuredAnswer),
      )
    : part.text;
  return <AssistantMarkdownText {...part} text={text} evidence={executionTrace} animate={active} />;
};

const TeamProgressParts: FC = () => {
  return (
    <TeamCollaborationView />
  );
};

const NativeExecutionDisclosure: FC<{
  children?: ReactNode;
  label?: string;
  forceOpen?: boolean;
}> = ({ children, label = '执行过程', forceOpen = false }) => {
  const messageStatus = useMessage((state) => state.status?.type);
  const active = messageStatus === 'running' || messageStatus === 'requires-action';
  const messageTiming = useMessageTiming();
  const persistedDuration = useMessage((state) => state.metadata?.custom?.agent_run_duration_ms);
  const stageData = useMessage((state) => state.metadata?.unstable_data);
  const events = useMemo(
    () => reconcileTerminalStageEvents(agentStageEvents(stageData)),
    [stageData],
  );
  const eventDurationMs = useMemo(() => agentStageDurationMs(agentStageEvents(stageData)), [stageData]);
  const streamDurationMs = typeof messageTiming?.totalStreamTime === 'number'
    && Number.isFinite(messageTiming.totalStreamTime)
    && messageTiming.totalStreamTime >= 0
    ? messageTiming.totalStreamTime
    : undefined;
  // Persisted server events remain valid across reconnection and hydration;
  // a client stream timer can include time outside this run.
  const durationMs = typeof persistedDuration === 'number' && Number.isFinite(persistedDuration) && persistedDuration >= 0
    ? persistedDuration
    : eventDurationMs ?? streamDurationMs;
  const durationLabel = durationMs == null ? '—' : formatElapsedDuration(durationMs);
  const compactLabel = active
    ? '执行中'
    : `${label === '执行过程' ? '' : `${label} · `}用时 ${durationLabel}`;
  const detailId = useId();
  const [expandedOverride, setExpandedOverride] = useState<boolean | null>(null);
  const expanded = active || (forceOpen && expandedOverride !== false) || expandedOverride === true;

  return (
    <section className="relative mb-3 min-w-0" aria-label={label}>
      <NativeDisclosure
        open={expanded}
        onToggle={() => setExpandedOverride((value) => value === true ? false : true)}
        detailId={detailId}
        ariaLabel={`${label}详情`}
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
        </div>
      </NativeDisclosure>
    </section>
  );
};

const NativeAgentStagePart: FC<DataMessagePartProps> = ({ data }) => {
  const event = agentStageEvents([data]).at(-1);
  if (!event) return null;
  return (
    <div data-agent-display-part="stage">
      <AgentStageIndicator event={event} />
    </div>
  );
};

const NativeStockChartPart: FC<DataMessagePartProps> = ({ data }) => {
  const reference = normalizeChartReference(data);
  if (!reference) return null;
  return (
    <div data-agent-display-part="chart">
      <ChartReference reference={reference} />
    </div>
  );
};

const NativeAnswerBoundaryPart: FC<DataMessagePartProps> = () => null;

const NativeAssistantParts: FC = () => {
  return <MessagePrimitive.Parts
    unstable_showEmptyOnNonTextEnd={false}
    components={{
      Text: NativeTextPart,
      Reasoning: () => null,
      tools: { Fallback: AgentToolCallPart },
      data: {
        by_name: {
          'agent-stage': NativeAgentStagePart,
          'stock-chart': NativeStockChartPart,
          'agent-answer-boundary': NativeAnswerBoundaryPart,
        },
        Fallback: () => null,
      },
      ToolGroup: NativeToolGroup,
      ReasoningGroup: InlineMessagePartGroup,
    }}
  />;
};

const AssistantMessage: FC<{ productMode: AgentProductMode }> = ({ productMode }) => {
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
  const publishedAnswerText = useMessage((s) => publishedAnswerTextFromContent(s.content));
  const evidenceTrace = useMessage((s) => (
    s.metadata?.custom?.agent_execution_trace
    ?? s.metadata?.custom?.agentExecutionTrace
  ));
  const hasTeamTerminalFailure = useMessage((s) => {
    if (productMode !== 'team') return false;
    const rawTrace = s.metadata?.custom?.agent_execution_trace
      ?? s.metadata?.custom?.agentExecutionTrace;
    if (!isRecord(rawTrace) || !isRecord(rawTrace.team)) return false;
    const team = rawTrace.team;
    const failure = team.failure;
    const status = typeof team.status === 'string' ? team.status : '';
    // A partial Team run is still a terminal, inspectable outcome. Keep its
    // collaboration process mounted after the final answer so the user can
    // expand the worker tools and the exact runtime gap instead of losing the
    // whole Team projection at publish time.
    return isRecord(failure) || ['partial', 'failed', 'blocked', 'cancelled'].includes(status);
  });
  const structuredAnswer = structuredAnswerFromTrace(evidenceTrace);
  const hasTeamMessage = useMessage((s) => {
    const trace = s.metadata?.custom?.agent_execution_trace
      ?? s.metadata?.custom?.agentExecutionTrace;
    return Boolean(
      isActive && productMode === 'team'
    ) || Boolean(
      trace && typeof trace === 'object' && !Array.isArray(trace)
        && 'team' in trace
        && trace.team,
    ) || agentStageEvents(s.metadata?.unstable_data).some((event) => (
      event.details?.team_id || event.details?.teamId
    ));
  });
  const displayAnswerText = stripStructuredAnswerReferenceFallbacks(
    productMode === 'team' && hasTeamMessage ? publishedAnswerText : answerText,
    structuredAnswer,
  );
  // A Team answer can arrive in the same transport update that moves the
  // message to its terminal status.  Using only `isActive` would render that
  // answer in one paint, which is the abrupt final block seen in the chat.
  // A mounted live message starts without the accepted answer, so animate
  // only when the answer appears after mount. A hydrated historical message
  // already has answer text on its first render and therefore does not replay
  // the typewriter animation after refresh.
  const answerPresentOnMount = useRef(Boolean(displayAnswerText.trim()));
  const answerArrivedAfterMount = Boolean(displayAnswerText.trim()) && !answerPresentOnMount.current;
  const animateAnswer = isActive || answerArrivedAfterMount;
  const hasOrderedPart = useMessage((s) => s.content.some((part) => {
    if (part.type === 'tool-call') return true;
    if (part.type === 'data') {
      return part.name === 'agent-stage'
        || part.name === 'stock-chart'
        || part.name === 'team-model-projection';
    }
    // The default assistant-ui reasoning renderer is intentionally hidden;
    // private reasoning alone must not suppress the pending/stage fallback.
    if (part.type === 'text') return part.text.trim().length > 0;
    return false;
  }));
  const hasNativeDisplayPart = useMessage((s) => s.content.some((part) => {
    if (part.type === 'tool-call') return true;
    if (part.type === 'data') {
      return part.name === 'agent-stage'
        || part.name === 'stock-chart'
        || part.name === 'team-model-projection'
        || part.name === 'agent-answer-boundary';
    }
    if (part.type === 'text') {
      return part.text.trim().length > 0 && (isActive || hasDisplayMetadata(part));
    }
    return false;
  }));
  const hasNativeToolPart = useMessage((s) => s.content.some((part) => part.type === 'tool-call'));
  const hasNativeAnswerPart = useMessage((s) => s.content.some((part) => (
    part.type === 'text' && displayKindOf(part) === 'answer'
  )));
  const hasNativeProcessPart = useMessage((s) => s.content.some((part) => {
    if (part.type === 'tool-call') return true;
    if (part.type === 'data') {
      return part.name === 'agent-stage' || part.name === 'team-model-projection';
    }
    if (part.type === 'text') {
      return displayKindOf(part) !== 'answer' && part.text.trim().length > 0;
    }
    return false;
  }));
  const hasNativeChartPart = useMessage((s) => s.content.some((part) => (
    part.type === 'data' && part.name === 'stock-chart'
  )));
  const hasTeamProgressPart = useMessage((s) => s.content.some((part) => (
    (part.type === 'text'
      && displayKindOf(part) !== 'answer'
      && part.text.trim().length > 0)
    || (part.type === 'data' && part.name === 'team-model-projection')
  )));
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
        <div className="w-full min-w-0 overflow-hidden text-[17px] leading-7 text-foreground sm:text-[18px]">
          {hasTeamMessage ? (
            <>
              {hasTeamProgressPart || isActive || hasTeamMessage ? (
                <NativeExecutionDisclosure
                  label={hasTeamTerminalFailure ? 'Team 协作终态' : 'Team 协作过程'}
                  forceOpen={hasTeamTerminalFailure}
                >
                  <TeamProgressParts />
                </NativeExecutionDisclosure>
              ) : null}
              {displayAnswerText.trim() ? (
                <AssistantMarkdown
                  text={displayAnswerText}
                  evidence={evidenceTrace}
                  animate={animateAnswer}
                />
              ) : null}
              <StructuredAnswerReferences
                answer={structuredAnswer}
                renderedText={displayAnswerText}
                // TeamBoard owns native stock-chart parts so charts stay with
                // their member workspace. The structured answer still owns
                // references that do not have a native display part.
                renderCharts={!hasNativeChartPart}
                renderActions={!hasNativeToolPart}
              />
            </>
          ) : isActive || hasNativeDisplayPart ? (
            <>
              {hasNativeDisplayPart ? (
                <NativeAssistantParts />
              ) : hasNativeProcessPart ? (
                <NativeExecutionDisclosure>
                  <AgentExecutionTimeline
                    presentation="inline"
                    stageOnly
                    nativeProgress={hasNativeProcessPart}
                  />
                </NativeExecutionDisclosure>
              ) : null}
              {!hasNativeDisplayPart && hasNativeAnswerPart ? (
                <AssistantMarkdown
                  text={displayAnswerText}
                  evidence={evidenceTrace}
                  animate={animateAnswer}
                />
              ) : null}
              <StructuredAnswerReferences
                answer={structuredAnswer}
                renderedText={displayAnswerText}
                renderCharts={!hasNativeChartPart}
                renderActions={!hasNativeToolPart}
              />
              {isActive && !hasNativeDisplayPart && !hasOrderedPart && hasExecutionRecord && hasActiveExecutionDetail ? (
                <AgentExecutionTimeline reasoningText={reasoningText} />
              ) : null}
              {isActive && !hasNativeDisplayPart && !hasOrderedPart && !hasNativeProcessPart && !hasVisibleContent && !hasActiveExecutionDetail ? (
                <AssistantPendingIndicator />
              ) : null}
            </>
          ) : (
            <>
              <AgentExecutionTimeline reasoningText={reasoningText} />
              <AssistantMarkdown text={displayAnswerText} evidence={evidenceTrace} animate={animateAnswer} />
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

const GuardedAssistantMessage: FC<{ productMode: AgentProductMode }> = ({ productMode }) => {
  const resetKey = useMessage((s) => {
    const stageCount = Array.isArray(s.metadata?.unstable_data) ? s.metadata.unstable_data.length : 0;
    return `${s.id}:${s.status?.type || 'idle'}:${s.content.length}:${stageCount}`;
  });
  return (
    <AssistantMessageBoundary resetKey={resetKey}>
      <AssistantMessage productMode={productMode} />
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
