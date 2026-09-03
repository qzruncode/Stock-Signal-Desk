import type React from 'react';
import { useEffect, useRef } from 'react';
import { useThread, useThreadRuntime } from '@assistant-ui/react';
import type { ThreadMessageLike } from '@assistant-ui/core';
import type { ReadonlyJSONValue } from 'assistant-stream/utils';
import { type AgentExecutionTrace, type ChatConversationDetail } from '../../api/agent';

/**
 * ChatRuntimeBridge:把后端会话详情(ChatConversationDetail)桥接到 assistant-ui
 * 的 ThreadRuntime。负责规范化 hydration、续流(startRun)与生成态判定。
 *
 * 从 ChatHomePage 抽出,使页面文件守住 600 行预算;展示片段转换函数保持
 * 纯函数，供 hydration 测试直接验证，不在组件里复制一套解析逻辑。
 */

const PENDING_ASSISTANT_SUFFIX = '-assistant-pending';
const NON_ANSWER_ASSISTANT_MESSAGES = new Set([
  '上游模型服务返回超时；已保留已有工具观察和证据。',
  '模型服务暂时不可用；已保留已有工具观察和证据。',
  '本轮工具/循环预算已耗尽；已保留已有观察并停止继续调用。',
  '本轮模型调用、Token 或费用预算已耗尽；已保留已有工具观察和证据。',
]);
const TERMINAL_RUN_STATUSES = new Set([
  'completed',
  'partial',
  'failed',
  'cancelled',
  'blocked',
]);

const normalizeMessageRole = (role: string): 'user' | 'assistant' | 'system' => {
  if (role === 'user' || role === 'assistant' || role === 'system') {
    return role;
  }
  return 'assistant';
};

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

const stageEventKey = (value: unknown): string => {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return String(value);
  const record = value as Record<string, unknown>;
  return [
    record.event,
    record.run_id ?? record.runId,
    record.stage,
    record.status,
    record.action_id ?? record.actionId ?? record.task_id ?? record.taskId,
    record.tool_call_id ?? record.toolCallId,
    record.round_id ?? record.roundId,
    record.occurred_at ?? record.occurredAt,
    record.summary,
  ].map((item) => String(item ?? '')).join('|');
};

const persistedStageEvents = (
  latestStage: NonNullable<ChatConversationDetail['resumeState']>['latestStage'],
  executionTrace?: AgentExecutionTrace | null,
): ReadonlyJSONValue[] => {
  const candidates = [
    ...(executionTrace?.stages || []),
    ...(latestStage ? [latestStage] : []),
  ] as unknown as ReadonlyJSONValue[];
  const seen = new Set<string>();
  return candidates.filter((event) => {
    const key = stageEventKey(event);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
};

const hasPersistedExecution = (
  persistedStages: ReadonlyJSONValue[],
  executionTrace?: AgentExecutionTrace | null,
): boolean => {
  if (persistedStages.length > 0) return true;
  if (!executionTrace) return false;
  return Object.values(executionTrace).some((value) => {
    if (Array.isArray(value)) return value.length > 0;
    return Boolean(value && typeof value === 'object' && Object.keys(value).length > 0);
  });
};

type RuntimeContentPart = Extract<ThreadMessageLike['content'], readonly unknown[]>[number];

const displayPartProviderMetadata = (part: Record<string, unknown>): Record<string, Record<string, string>> => {
  const metadata: Record<string, string> = {
    displayKind: String(part.displayKind ?? part.display_kind ?? 'progress'),
  };
  const roundId = part.roundId ?? part.round_id;
  if (roundId !== undefined && roundId !== null && roundId !== '') {
    metadata.roundId = String(roundId);
  }
  return { dsa: metadata };
};

const displayPartsForRuntime = (
  executionTrace: AgentExecutionTrace | null | undefined,
  canonicalAnswer: string,
): RuntimeContentPart[] => {
  const rawParts = executionTrace?.displayParts;
  if (!Array.isArray(rawParts)) return [];
  let answerPartSeen = false;
  const parts: RuntimeContentPart[] = [];

  rawParts.forEach((rawPart) => {
    if (!isRecord(rawPart)) return;
    const type = String(rawPart.type || '');
    if (type === 'text') {
      const displayKind = String(rawPart.displayKind ?? rawPart.display_kind ?? 'progress');
      const rawText = String(rawPart.text || '');
      const text = displayKind === 'answer' && canonicalAnswer
        ? canonicalAnswer
        : rawText;
      if (!text) return;
      if (displayKind === 'answer') answerPartSeen = true;
      parts.push({
        type: 'text',
        text,
        providerMetadata: displayPartProviderMetadata(rawPart),
        ...(rawPart.parentId || rawPart.parent_id
          ? { parentId: String(rawPart.parentId ?? rawPart.parent_id) }
          : {}),
      } as RuntimeContentPart);
      return;
    }
    if (type !== 'tool-call') return;
    const toolCallId = String(rawPart.toolCallId ?? rawPart.tool_call_id ?? '');
    const toolName = String(rawPart.toolName ?? rawPart.tool_name ?? '原子工具');
    if (!toolCallId) return;
    const toolPart: Record<string, unknown> = {
      type: 'tool-call',
      toolCallId,
      toolName,
      argsText: String(rawPart.argsText ?? rawPart.args_text ?? ''),
      providerMetadata: displayPartProviderMetadata(rawPart),
    };
    const parentId = rawPart.parentId ?? rawPart.parent_id;
    if (parentId) toolPart.parentId = String(parentId);
    if (Object.prototype.hasOwnProperty.call(rawPart, 'result')) {
      toolPart.result = rawPart.result;
    }
    if (Object.prototype.hasOwnProperty.call(rawPart, 'isError')
      || Object.prototype.hasOwnProperty.call(rawPart, 'is_error')) {
      toolPart.isError = Boolean(rawPart.isError ?? rawPart.is_error);
    }
    parts.push(toolPart as RuntimeContentPart);
  });

  if (canonicalAnswer && !answerPartSeen) {
    parts.push({
      type: 'text',
      text: canonicalAnswer,
      providerMetadata: { dsa: { displayKind: 'answer' } },
    } as RuntimeContentPart);
  }
  return parts;
};

const isTerminalRunStatus = (status: string | null | undefined): boolean => (
  typeof status === 'string' && TERMINAL_RUN_STATUSES.has(status)
);

export const toRuntimeMessages = (
  conversationId: string,
  messages: ChatConversationDetail['messages'],
  latestStage?: NonNullable<ChatConversationDetail['resumeState']>['latestStage'],
  executionTrace?: AgentExecutionTrace | null,
  runId?: string | null,
  assistantText?: string,
  includeTracePlaceholder = false,
): ThreadMessageLike[] => {
  const persistedStages = persistedStageEvents(latestStage, executionTrace);
  const renderableMessages = messages.filter((message) => (
    message.role !== 'assistant'
    || !NON_ANSWER_ASSISTANT_MESSAGES.has((message.content || '').trim())
  ));
  // A durable trace belongs to the assistant message created by that same
  // terminal run, never merely to the latest historical assistant message.
  // This prevents a failed/cancelled turn with no answer text from decorating
  // a previous answer as if it were still executing.
  const normalizedAssistantText = (assistantText || '').trim();
  const traceAssistantId = normalizedAssistantText
    ? [...renderableMessages].reverse().find((message) => (
        message.role === 'assistant' && (message.content || '').trim() === normalizedAssistantText
      ))?.id
    : undefined;
  const runtimeMessages = renderableMessages
    .filter((message) => (message.content || '').trim().length > 0)
    .map((message) => {
      const stages = message.id === traceAssistantId ? persistedStages : [];
      const displayParts = message.id === traceAssistantId
        ? displayPartsForRuntime(executionTrace, normalizedAssistantText)
        : [];
      return {
        id: message.id,
        role: normalizeMessageRole(message.role),
        createdAt: message.createdAt ? new Date(message.createdAt) : new Date(),
        content: displayParts.length > 0
          ? displayParts
          : [{ type: 'text' as const, text: message.content || '' }],
        ...(
          (stages.length > 0 || (message.id === traceAssistantId && executionTrace))
            ? {
                metadata: {
                  ...(stages.length > 0 ? { unstable_data: stages } : {}),
                  ...(message.id === traceAssistantId && executionTrace
                    ? { custom: { agent_execution_trace: executionTrace } }
                    : {}),
                },
              }
            : {}
        ),
      };
    });
  // A failed/cancelled run can legitimately have no assistant text at all.
  // Keep its durable trace visible as a dedicated assistant record rather
  // than replaying obsolete stream chunks merely to recreate tool UI.
  if (
    includeTracePlaceholder
    && !traceAssistantId
    && hasPersistedExecution(persistedStages, executionTrace)
  ) {
    const stageRunId = latestStage?.runId ?? latestStage?.run_id;
    runtimeMessages.push({
      id: `${conversationId}-agent-trace-${runId || stageRunId || 'latest'}`,
      role: 'assistant',
      createdAt: new Date(),
      content: displayPartsForRuntime(executionTrace, normalizedAssistantText),
      metadata: {
        ...(persistedStages.length > 0 ? { unstable_data: persistedStages } : {}),
        ...(executionTrace ? { custom: { agent_execution_trace: executionTrace } } : {}),
      },
    });
  }
  return runtimeMessages;
};

const getConversationHydrationKey = (detail: ChatConversationDetail): string => {
  const lastMessage = detail.messages.at(-1);
  const executionTrace = detail.executionTrace ?? detail.resumeState?.executionTrace;
  return [
    detail.id,
    detail.updatedAt,
    detail.isGenerating ? 'running' : 'idle',
    detail.resumeState?.active ? 'resumable' : 'not-resumable',
    detail.resumeState?.status ?? '',
    detail.resumeState?.latestStage?.stage ?? '',
    detail.resumeState?.latestStage?.status ?? '',
    detail.resumeState?.latestStage?.occurredAt
      ?? detail.resumeState?.latestStage?.occurred_at
      ?? '',
    executionTrace?.stages?.length ?? 0,
    stageEventKey(executionTrace?.stages?.at(-1)),
    executionTrace?.displayParts?.length ?? 0,
    stageEventKey(executionTrace?.displayParts?.at(-1)),
    executionTrace?.toolResults?.length ?? 0,
    executionTrace?.evidence?.length ?? 0,
    executionTrace?.claimEvidence?.length ?? 0,
    detail.resumeState?.afterChunkIndex ?? '',
    detail.pendingInterrupt?.interruptId ?? '',
    detail.pendingInterrupt?.fingerprint ?? '',
    detail.messages.length,
    lastMessage?.id ?? '',
    lastMessage?.content ?? '',
  ].join('|');
};

const removeTrailingAssistant = (
  messages: ChatConversationDetail['messages'],
): ChatConversationDetail['messages'] => {
  const lastMessage = messages.at(-1);
  if (lastMessage?.role !== 'assistant') {
    return messages;
  }
  return messages.slice(0, -1);
};

export type ChatRuntimeBridgeProps = {
  conversationDetail: ChatConversationDetail | null;
  onThreadRuntime: (threadRuntime: ReturnType<typeof useThreadRuntime>) => void;
  onPrepareResumeExisting: (conversationId: string, afterChunkIndex: number | null) => void;
  /**
   * A terminal snapshot is only allowed to detach a local stream that is
   * actually attached to that durable run.  A user can send a new turn while
   * the selected detail still describes the preceding terminal run.
   */
  shouldDetachTerminalStream?: (conversationId: string, runId: string | null | undefined) => boolean;
};

const detachTerminalStreamByDefault = () => true;

export const ChatRuntimeBridge: React.FC<ChatRuntimeBridgeProps> = ({
  conversationDetail,
  onThreadRuntime,
  onPrepareResumeExisting,
  shouldDetachTerminalStream = detachTerminalStreamByDefault,
}) => {
  const threadRuntime = useThreadRuntime();
  const isThreadRunning = useThread((state) => state.isRunning);
  const appliedHydrationKeyRef = useRef<string | null>(null);
  const appliedConversationIdRef = useRef<string | null>(null);
  const pendingConversationIdRef = useRef<string | null>(null);
  const terminalDetachKeyRef = useRef<string | null>(null);

  useEffect(() => {
    onThreadRuntime(threadRuntime);
  }, [onThreadRuntime, threadRuntime]);

  useEffect(() => {
    if (!conversationDetail) {
      appliedHydrationKeyRef.current = null;
      appliedConversationIdRef.current = null;
      pendingConversationIdRef.current = null;
      terminalDetachKeyRef.current = null;
      onPrepareResumeExisting('', null);
      threadRuntime.cancelRun();
      threadRuntime.reset([]);
      return;
    }

    // A delayed detail request can resolve after the user has already sent a
    // message.  Hydrating that stale snapshot would call cancelRun/reset and
    // erase the live assistant turn. Keep the local run authoritative for the
    // same conversation. For another conversation, cancel the local stream
    // first and wait for assistant-ui to finish its abort update before reset.
    // Resetting immediately removes the old parent message while its stream
    // callback can still write to it, which throws "Parent message not found".
    const isConversationChange = (
      appliedConversationIdRef.current !== null
      && appliedConversationIdRef.current !== conversationDetail.id
    );
    const hydrationKey = getConversationHydrationKey(conversationDetail);
    const serverReportedTerminal = isTerminalRunStatus(
      conversationDetail.resumeState?.status,
    );
    if (isThreadRunning && isConversationChange) {
      if (pendingConversationIdRef.current !== conversationDetail.id) {
        pendingConversationIdRef.current = conversationDetail.id;
        onPrepareResumeExisting('', null);
        threadRuntime.cancelRun();
      }
      return;
    }
    if (isThreadRunning && !isConversationChange) {
      // A stale local stream must never outlive a durable terminal result.
      // This can happen when the API restarts between two data-stream chunks:
      // the server completes safely, while the client-side reader still says
      // it is running.  Cancel once and let the next non-running render
      // hydrate the canonical terminal trace/message.
      if (
        serverReportedTerminal
        && shouldDetachTerminalStream(
          conversationDetail.id,
          conversationDetail.resumeState?.runId,
        )
      ) {
        if (terminalDetachKeyRef.current !== hydrationKey) {
          terminalDetachKeyRef.current = hydrationKey;
          threadRuntime.cancelRun();
        }
        return;
      }
      // Record which conversation owns the live runtime even when its stale
      // server snapshot must not be applied. A later id change can then detach
      // this stream safely.
      appliedConversationIdRef.current = conversationDetail.id;
      return;
    }

    terminalDetachKeyRef.current = null;
    if (appliedHydrationKeyRef.current === hydrationKey) {
      return;
    }
    appliedHydrationKeyRef.current = hydrationKey;
    appliedConversationIdRef.current = conversationDetail.id;
    pendingConversationIdRef.current = null;

    onPrepareResumeExisting(conversationDetail.id, null);
    threadRuntime.cancelRun();
    threadRuntime.reset([]);

    const isGenerating = conversationDetail.isGenerating === true;
    const isWaitingForApproval = Boolean(conversationDetail.pendingInterrupt);
    const resumeStatus = conversationDetail.resumeState?.status;
    // Protect the UI against stale API payloads as well as the backend
    // contract: terminal history is display data, never a live stream.
    const canReplayStream = !isTerminalRunStatus(resumeStatus) && (
      isGenerating || conversationDetail.resumeState?.active === true
    );
    const latestStage = conversationDetail.resumeState?.latestStage;
    // The conversation-level trace is canonical.  Older server payloads may
    // still carry the same trace in resumeState, so retain it only as a
    // fallback instead of traversing/rendering two deep copies.
    const executionTrace = conversationDetail.executionTrace
      ?? conversationDetail.resumeState?.executionTrace;
    const shouldReplayStream = !isWaitingForApproval && canReplayStream;
    const pendingId = `${conversationDetail.id}${PENDING_ASSISTANT_SUFFIX}`;

    // `thread_state` is an opaque assistant-ui snapshot. It can contain old
    // tool result shapes and arbitrarily large payloads, so it is never
    // re-imported as rendering state. Canonical messages plus the durable
    // trace for this exact run are the only display inputs.
    const messagesWithoutPending = conversationDetail.messages.filter((m) => m.id !== pendingId);
    const visibleMessages = shouldReplayStream
      ? removeTrailingAssistant(messagesWithoutPending)
      : conversationDetail.messages;

    threadRuntime.reset(toRuntimeMessages(
      conversationDetail.id,
      visibleMessages,
      latestStage,
      executionTrace,
      conversationDetail.resumeState?.runId,
      conversationDetail.resumeState?.assistantText,
      !shouldReplayStream,
    ));

    if (!shouldReplayStream) {
      // A normal send starts its run inside assistant-ui before hydration.  If
      // persisted history ends with a user message while the backend reports
      // no resumable run, it represents a cancelled/failed/interrupted turn.
      // Starting here would silently resurrect Stop requests and can loop on
      // every snapshot refresh.  Only the resumable branch below may auto-run.
      return;
    }

    // 后端仍在生成或保留了刚完成的 run:通过 /agent/chat + resume_existing
    // 复用 useDataStreamRuntime 的完整 data-stream 管道,保证 K 线图等 tool UI
    // 与初次生成一致。
    const parentId = visibleMessages.at(-1)?.id ?? null;
    // The server chooses the cursor. A durable run may intentionally return
    // zero when the browser died before persisting rich tool/reasoning parts;
    // replay then reconstructs those parts from the ordered event log.
    onPrepareResumeExisting(
      conversationDetail.id,
      conversationDetail.resumeState?.afterChunkIndex ?? 0,
    );
    threadRuntime.startRun({
      parentId,
      sourceId: parentId,
      runConfig: {},
    });
  }, [
    conversationDetail,
    isThreadRunning,
    threadRuntime,
    onPrepareResumeExisting,
    shouldDetachTerminalStream,
  ]);

  return null;
};
