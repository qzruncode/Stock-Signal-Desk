import type { ErrorInfo, FC, ReactNode } from 'react';
import {
  Component,
} from 'react';
import {
  AuiIf,
  ThreadPrimitive,
  MessagePrimitive,
  ActionBarPrimitive,
  useMessage,
  useAuiState,
} from '@assistant-ui/react';
import {
  ChevronDownIcon,
  CopyIcon,
  RefreshCwIcon,
  DownloadIcon,
  Volume2Icon,
  SquareIcon as StopIcon,
} from 'lucide-react';
import { AssistantMarkdown } from './AssistantMarkdownText';
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
  assistantPublishedAnswerTextFromContent as publishedAnswerTextFromContent,
  hasAssistantDisplayMetadata as hasDisplayMetadata,
} from '../../utils/assistantAnswer';
import { cn } from '../../utils/cn';
import {
  agentStageEvents,
} from '../../utils/agentStage';
import { AgentExecutionTimeline } from './AgentReasoning';
import { AssistantTypingIndicator } from './AssistantTypingIndicator';
import { Composer } from './ThreadComposer';
import { EmptyState } from './ThreadEmptyState';
import { UserMessage } from './ThreadUserMessage';
import { TeamCollaborationView } from './TeamBoard';
import { NativeAssistantParts, NativeExecutionDisclosure } from './ThreadNativeParts';
import { GoalMessageContent } from './GoalMessageContent';
import { isRecord } from './AgentReasoningUtils';
import { terminalRunFailureNotice } from './ChatRuntimeBridgeUtils';
import type { AgentProductMode } from '../../utils/agentMode';

/* ── Thread (root) ───────────────────────────────────────────────────── */

const Thread: FC<{
  onUserCancel?: () => void;
  onDeleteUserTurn?: (messageId: string) => void;
  agentMode: AgentProductMode;
  onAgentModeChange: (mode: AgentProductMode) => void;
  knowledgeBaseIds: string[];
  knowledgeSelectionDisabled: boolean;
  onKnowledgeBaseIdsChange: (ids: string[]) => void;
}> = ({
  onUserCancel,
  onDeleteUserTurn,
  agentMode,
  onAgentModeChange,
  knowledgeBaseIds,
  knowledgeSelectionDisabled,
  onKnowledgeBaseIdsChange,
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
        knowledgeBaseIds={knowledgeBaseIds}
        knowledgeSelectionDisabled={knowledgeSelectionDisabled}
        onKnowledgeBaseIdsChange={onKnowledgeBaseIdsChange}
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
  const terminalFailureNotice = useMessage((s) => terminalRunFailureNotice(
    s.metadata?.custom?.agent_execution_trace
      ?? s.metadata?.custom?.agentExecutionTrace,
    s.metadata?.unstable_data,
    s.metadata?.custom?.agent_run_status
      ?? s.metadata?.custom?.agentRunStatus,
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
  const hasGoalMessage = useMessage((s) => {
    const rawTrace = s.metadata?.custom?.agent_execution_trace
      ?? s.metadata?.custom?.agentExecutionTrace;
    return Boolean(isActive && productMode === 'goal')
      || Boolean(
        rawTrace && typeof rawTrace === 'object' && !Array.isArray(rawTrace)
          && 'goal' in rawTrace
          && rawTrace.goal,
      )
      || s.content.some((part) => (
        part.type === 'data' && part.name === 'agent-model-projection'
        && isRecord(part.data) && part.data.scope === 'goal'
      ))
      || agentStageEvents(s.metadata?.unstable_data).some((event) => (
        event.stage.startsWith('goal.')
        || event.details?.kind === 'goal_intake'
        || event.details?.kind === 'goal_confirm'
        || event.details?.kind === 'goal_action_select'
        || event.details?.kind === 'goal_execute'
        || event.details?.kind === 'goal_observe'
        || event.details?.kind === 'goal_monitor'
        || event.details?.kind === 'goal_finalize'
      ));
  });
  const displayAnswerText = stripStructuredAnswerReferenceFallbacks(
    productMode === 'team' && hasTeamMessage ? publishedAnswerText : answerText,
    structuredAnswer,
  );
  // Progressive text is only a live-generation affordance. Once the message
  // reaches a terminal status, render the canonical answer immediately; a
  // background tab may throttle animation frames indefinitely.
  const animateAnswer = isActive;
  const hasOrderedPart = useMessage((s) => s.content.some((part) => {
    if (part.type === 'tool-call') return true;
    if (part.type === 'data') {
      return part.name === 'agent-stage'
        || part.name === 'stock-chart'
        || part.name === 'team-model-projection'
        || part.name === 'agent-model-projection';
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
        || part.name === 'agent-model-projection'
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
      return part.name === 'agent-stage'
        || part.name === 'team-model-projection'
        || part.name === 'agent-model-projection';
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
          {hasGoalMessage ? (
            <GoalMessageContent />
          ) : hasTeamMessage ? (
            <>
              {hasTeamProgressPart || isActive || hasTeamMessage ? (
                <NativeExecutionDisclosure
                  label={hasTeamTerminalFailure ? 'Team 协作终态' : 'Team 协作过程'}
                  forceOpen={hasTeamTerminalFailure}
                >
                  <TeamCollaborationView />
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
              {isActive && !hasNativeAnswerPart ? (
                <AssistantTypingIndicator />
              ) : null}
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
            </>
          ) : (
            <>
              <AgentExecutionTimeline reasoningText={reasoningText} />
              <AssistantMarkdown text={displayAnswerText} evidence={evidenceTrace} animate={animateAnswer} />
              <StructuredAnswerReferences answer={structuredAnswer} renderedText={displayAnswerText} />
            </>
          )}
          {terminalFailureNotice ? (
            <div
              role="alert"
              className="mt-2 rounded-lg border border-amber-300/60 bg-amber-50/70 px-3 py-2 text-sm leading-5 text-amber-900"
            >
              {terminalFailureNotice}
            </div>
          ) : null}
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

export default Thread;
