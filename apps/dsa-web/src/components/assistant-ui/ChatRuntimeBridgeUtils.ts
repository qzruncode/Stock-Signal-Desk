import type { ThreadMessageLike } from '@assistant-ui/core';
import type { ReadonlyJSONValue } from 'assistant-stream/utils';
import type { AgentExecutionTrace, ChatConversationDetail } from '../../api/agent';

export const PENDING_ASSISTANT_SUFFIX = '-assistant-pending';

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

export const isTerminalRunStatus = (status: string | null | undefined): boolean => (
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

export const getConversationHydrationKey = (detail: ChatConversationDetail): string => {
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

export const removeTrailingAssistant = (
  messages: ChatConversationDetail['messages'],
): ChatConversationDetail['messages'] => {
  const lastMessage = messages.at(-1);
  if (lastMessage?.role !== 'assistant') {
    return messages;
  }
  return messages.slice(0, -1);
};
