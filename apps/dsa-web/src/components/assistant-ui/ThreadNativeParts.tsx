import type { FC, ReactNode } from 'react';
import { createContext, useContext, useId, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { MessagePrimitive, useMessage, useMessagePartRuntime, useMessageTiming, useScrollLock } from '@assistant-ui/react';
import type { DataMessagePartProps, TextMessagePartProps } from '@assistant-ui/react';
import { ChevronRightIcon } from 'lucide-react';
import { AssistantMarkdown, AssistantMarkdownText } from './AssistantMarkdownText';
import { ChartReference } from './StructuredAnswerReferences';
import { normalizeChartReference, stripNativeAnswerReferenceFallbacks, stripStructuredAnswerReferenceFallbacks, structuredAnswerFromTrace } from './StructuredAnswerReferencesUtils';
import { assistantDisplayKindOf as displayKindOf } from '../../utils/assistantAnswer';
import { cn } from '../../utils/cn';
import { agentStageDurationMs, agentStageEvents } from '../../utils/agentStage';
import { formatElapsedDuration } from '../../utils/format';
import { AgentStageIndicator, AgentToolCallPart } from './AgentReasoning';
import { isRecord } from './AgentReasoningUtils';

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

const NativeAnswerAnimationContext = createContext(false);

const NativeTextPartContent: FC<TextMessagePartProps> = (part) => {
  const messageStatus = useMessage((state) => state.status?.type);
  const active = messageStatus === 'running' || messageStatus === 'requires-action';
  const animateAcceptedAnswer = useContext(NativeAnswerAnimationContext);
  // TextDeltaChunk intentionally carries only text, so the live answer's
  // display kind cannot be recovered from the text part itself.  The server
  // emits a boundary immediately before the accepted answer; use the ordered
  // part position to identify the text that follows it. Hydrated history
  // drops that marker, so completed messages do not replay the animation.
  const isLiveAnswerText = useMessage((state) => {
    let afterAnswerBoundary = false;
    return state.content.some((item) => {
      if (item.type === 'data' && item.name === 'agent-answer-boundary') {
        afterAnswerBoundary = true;
        return false;
      }
      return afterAnswerBoundary && item.type === 'text' && item.text === part.text;
    });
  });
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
  const animate = active || isLiveAnswerText || (
    animateAcceptedAnswer && displayKindOf(part) === 'answer'
  );
  return <AssistantMarkdownText {...part} text={text} evidence={executionTrace} animate={animate} />;
};

export const NativeExecutionDisclosure: FC<{
  children?: ReactNode;
  label?: string;
  forceOpen?: boolean;
}> = ({ children, label = '执行过程', forceOpen = false }) => {
  const messageStatus = useMessage((state) => state.status?.type);
  const active = messageStatus === 'running' || messageStatus === 'requires-action';
  const messageTiming = useMessageTiming();
  const persistedDuration = useMessage((state) => state.metadata?.custom?.agent_run_duration_ms);
  const stageData = useMessage((state) => state.metadata?.unstable_data);
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

const modelProgressDelimiters: Record<string, string> = {
  '(': ')',
  '（': '）',
  '[': ']',
  '［': '］',
  '【': '】',
  '{': '}',
  '｛': '｝',
};

/** Hide legacy/provisional model projections that are visibly incomplete. */
const isRenderableModelProgress = (value: string): boolean => {
  const text = value.trim();
  if (!/[。！？!?.]$/.test(text)) return false;
  const closing = new Set(Object.values(modelProgressDelimiters));
  const stack: string[] = [];
  for (const character of text) {
    if (modelProgressDelimiters[character]) {
      stack.push(modelProgressDelimiters[character]);
    } else if (closing.has(character)) {
      if (stack.pop() !== character) return false;
    }
  }
  return stack.length === 0;
};

export const NativeAgentModelProjectionPart: FC<DataMessagePartProps> = ({ data }) => {
  const active = useMessage((state) => (
    state.status?.type === 'running' || state.status?.type === 'requires-action'
  ));
  const initialData = isRecord(data) ? data : null;
  const projectionIdValue = initialData?.projection_id ?? initialData?.projectionId;
  const projectionId = typeof projectionIdValue === 'string' ? projectionIdValue : '';
  const selector = useMessagePartRuntime().path.messagePartSelector;
  const projectionData = useMessage((state) => {
    if (!projectionId) return initialData;
    let firstIndex = -1;
    let latest: Record<string, unknown> | null = null;
    state.content.forEach((part, index) => {
      if (part.type !== 'data' || part.name !== 'agent-model-projection' || !isRecord(part.data)) {
        return;
      }
      if ((part.data.projection_id ?? part.data.projectionId) !== projectionId) return;
      if (firstIndex < 0) firstIndex = index;
      if (!latest || Number(part.data.sequence ?? 0) >= Number(latest.sequence ?? 0)) latest = part.data;
    });
    // Read the updated text from the message subscription itself. Native
    // data props can lag by one render; comparing their old sequence with
    // the fresh message would otherwise hide the only mounted paragraph.
    // Legacy duplicate chunks also stay anchored at the first part's slot.
    if (selector.type === 'index' && selector.index !== firstIndex) return null;
    return latest;
  });
  if (!projectionData) return null;
  const projectionSource = projectionData.projection_source ?? projectionData.projectionSource;
  if (projectionSource !== 'model') return null;
  const text = typeof projectionData.text === 'string' ? projectionData.text.trim() : '';
  if (!text) return null;
  // Goal prose is intentionally incremental. Punctuation is not an
  // acceptance signal: hiding a growing sentence makes earlier text blink.
  if (projectionData.scope !== 'goal' && !isRenderableModelProgress(text)) return null;
  return (
    <div data-agent-display-part="model-projection">
      <AssistantMarkdown text={text} animate={projectionData.scope !== 'goal' && active} />
    </div>
  );
};

const NativeAnswerBoundaryPart: FC<DataMessagePartProps> = () => null;

export const NativeAssistantParts: FC<{
  animateAcceptedAnswer?: boolean;
  includeStageParts?: boolean;
}> = ({
  animateAcceptedAnswer = false,
  includeStageParts = true,
}) => {
  return (
    <NativeAnswerAnimationContext.Provider value={animateAcceptedAnswer}>
      <MessagePrimitive.Parts
        unstable_showEmptyOnNonTextEnd={false}
        components={{
          Text: NativeTextPart,
          Reasoning: () => null,
          tools: { Fallback: AgentToolCallPart },
          data: {
            by_name: {
              'agent-stage': includeStageParts ? NativeAgentStagePart : NativeAnswerBoundaryPart,
              'stock-chart': NativeStockChartPart,
              'agent-model-projection': NativeAgentModelProjectionPart,
              'agent-answer-boundary': NativeAnswerBoundaryPart,
            },
            Fallback: () => null,
          },
          ToolGroup: NativeToolGroup,
          ReasoningGroup: InlineMessagePartGroup,
        }}
      />
    </NativeAnswerAnimationContext.Provider>
  );
};
