import type { ThreadMessageLike } from '@assistant-ui/core';
import type { ReadonlyJSONValue } from 'assistant-stream/utils';
import type {
  AgentExecutionTrace,
  AgentExecutionTraceRecord,
  ChatConversationDetail,
} from '../../api/agent';

export const PENDING_ASSISTANT_SUFFIX = '-assistant-pending';

const NON_ANSWER_ASSISTANT_MESSAGES = new Set([
  '上游模型服务返回超时；已保留已有工具观察和证据。',
  '模型服务暂时不可用；已保留已有工具观察和证据。',
  '本轮工具/循环预算已耗尽；已保留已有观察并停止继续调用。',
  '本轮模型调用、Token 或费用预算已耗尽；已保留已有工具观察和证据。',
]);

const INTERNAL_TERMINAL_DIAGNOSTIC_MARKERS = [
  'PlanningPlan',
  'PlanningRoute',
  'PlanningStepReport',
  'PlanningContractError',
  'planning_',
  'ValidationError',
  'Input should',
  'failed after',
  'function_calling',
];

/** Hide provider/contract diagnostics from chat while keeping the audit trace intact. */
const sanitizeAssistantTerminalDiagnostics = (content: string): string => (
  content.replace(
    /\[本轮结果存在未完成的核验：([^\]]*)\]/g,
    (full, detail: string) => {
      if (!INTERNAL_TERMINAL_DIAGNOSTIC_MARKERS.some((marker) => detail.includes(marker))) {
        return full;
      }
      return '[本轮结果存在未完成的核验：计划尚未完整结束，以下回答仅保留已核验结果，并明确说明未完成的步骤]';
    },
  )
);

const normalizeAnswerForTraceMatch = (content: string): string => (
  sanitizeAssistantTerminalDiagnostics(content)
    .replace(/【证据\s*[^】]+】/g, '')
    .replace(/\s+/g, ' ')
    .trim()
);

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

/**
 * Keep headings in a terminal answer renderable when adjacent structured
 * blocks do not include the blank line that Markdown requires.
 */
const normalizeAnswerMarkdownBoundaries = (
  text: string,
  seenSectionNumbers: Set<string>,
): string => {
  const withBoundaries = text.replace(
    /([^#\n])[ \t]*(#{1,6}[ \t]+(?=\S))/g,
    '$1\n\n$2',
  );
  return withBoundaries.replace(
    /(^|\n)#{2,6}[ \t]+((?:[0-9]+|[一二三四五六七八九十百千万零〇两]+)[、.．:：)）-][ \t]*)(?:[^\n]*)/g,
    (full, lineBreak: string, numberPrefix: string) => {
      const number = numberPrefix.match(/^(?:[0-9]+|[一二三四五六七八九十百千万零〇两]+)/)?.[0];
      if (!number || !seenSectionNumbers.has(number)) {
        if (number) seenSectionNumbers.add(number);
        return full;
      }
      return lineBreak;
    },
  );
};

/** A one-point server-generated line chart is usually a quote lookup, not a chart. */
const isUnusableServerChart = (value: unknown): boolean => {
  if (!isRecord(value)) return false;
  const chartType = String(value.chartType ?? value.chart_type ?? 'line').toLowerCase();
  if (chartType === 'bar') return false;
  const rows = value.data;
  const hasServerIdentity = Boolean(
    String(value.actionId ?? value.action_id ?? '').trim()
      || String(value.toolCallId ?? value.tool_call_id ?? '').trim(),
  );
  return hasServerIdentity && Array.isArray(rows) && rows.length < 2;
};

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

const normalizeProgressText = (value: string): string => (
  value.trim().replace(/\s+/g, ' ')
);

const progressNgrams = (value: string): Set<string> => {
  const grams = new Set<string>();
  for (let index = 0; index <= value.length - 3; index += 1) {
    grams.add(value.slice(index, index + 3));
  }
  return grams;
};

/** Treat wording-only progress revisions as one visible timeline event. */
const isNearDuplicateProgress = (candidate: string, previous: string): boolean => {
  if (!candidate || !previous) return false;
  if (candidate === previous || candidate.includes(previous)) return true;
  const shorter = candidate.length <= previous.length ? candidate : previous;
  const longer = candidate.length <= previous.length ? previous : candidate;
  // A later model turn may add a transition sentence around the same
  // observation; compare the substantive overlap instead of requiring equal
  // paragraph lengths.
  if (shorter.length < 24 || shorter.length / longer.length < 0.60) return false;

  const candidateGrams = progressNgrams(candidate);
  const previousGrams = progressNgrams(previous);
  if (candidateGrams.size === 0 || previousGrams.size === 0) return false;
  let intersection = 0;
  for (const gram of candidateGrams) {
    if (previousGrams.has(gram)) intersection += 1;
  }
  const union = candidateGrams.size + previousGrams.size - intersection;
  const jaccard = union > 0 ? intersection / union : 0;
  const overlap = intersection / Math.min(candidateGrams.size, previousGrams.size);
  return jaccard >= 0.68 || overlap >= 0.86;
};

const displayPartsForRuntime = (
  executionTrace: AgentExecutionTrace | null | undefined,
  canonicalAnswer: string,
): RuntimeContentPart[] => {
  const persistedParts = executionTrace?.displayParts;
  const rawParts = Array.isArray(persistedParts)
    ? persistedParts.filter(isRecord)
    : [];
  const replayParts = rawParts;
  // Team's terminal narrative has one owner: the committed chat answer.
  // A bounded execution trace is for the collaboration lanes, not a second
  // copy of the answer that may silently replace it with a truncated prefix.
  const canonicalTeamAnswer = Boolean(canonicalAnswer && executionTrace?.team);
  let answerPartSeen = false;
  const seenProgressTexts = new Set<string>();
  const seenAnswerSectionNumbers = new Set<string>();
  const parts: RuntimeContentPart[] = [];

  replayParts.forEach((rawPart) => {
    if (!isRecord(rawPart)) return;
    const type = String(rawPart.type || '');
    if (type === 'text') {
      const displayKind = String(rawPart.displayKind ?? rawPart.display_kind ?? 'progress');
      if (canonicalTeamAnswer && displayKind === 'answer') return;
      const rawText = String(rawPart.text || '');
      const normalizedText = displayKind === 'answer'
        ? normalizeAnswerMarkdownBoundaries(rawText, seenAnswerSectionNumbers)
        : rawText;
      const textCandidates = displayKind === 'progress'
        ? normalizedText.split(/\n\s*\n/).flatMap((block, index, blocks) => {
            const value = block.trim();
            if (!value) return [];
            const hasFollowingBlock = blocks.slice(index + 1).some((item) => item.trim());
            return [hasFollowingBlock || /\n\s*\n\s*$/.test(normalizedText) ? `${value}\n\n` : value];
          })
        : [normalizedText];
      textCandidates.forEach((text) => {
        if (!text) return;
        // Repeated provider fragments must not create duplicate visible
        // progress paragraphs in the current stream.
        if (displayKind === 'progress') {
          const normalizedProgress = normalizeProgressText(text);
          if ([...seenProgressTexts].some((previous) => (
            isNearDuplicateProgress(normalizedProgress, previous)
          ))) return;
          seenProgressTexts.add(normalizedProgress);
        }
        if (displayKind === 'answer') answerPartSeen = true;
        parts.push({
          type: 'text',
          text,
          providerMetadata: displayPartProviderMetadata(rawPart),
          ...(rawPart.parentId || rawPart.parent_id
            ? { parentId: String(rawPart.parentId ?? rawPart.parent_id) }
            : {}),
        } as RuntimeContentPart);
      });
      return;
    }
    if (type === 'data') {
      const name = String(rawPart.name || '').trim();
      // The answer boundary is a server-side replay marker, not visible UI.
      if (!name || name === 'agent-answer-boundary') return;
      if (name === 'stock-chart' && isUnusableServerChart(rawPart.data)) return;
      parts.push({
        type: 'data',
        name,
        data: (rawPart.data ?? null) as ReadonlyJSONValue,
        ...(rawPart.partId || rawPart.part_id
          ? { partId: String(rawPart.partId ?? rawPart.part_id) }
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

  if (canonicalAnswer && (canonicalTeamAnswer || !answerPartSeen)) {
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
  executionTraceHistory?: AgentExecutionTraceRecord[] | null,
): ThreadMessageLike[] => {
  const persistedStages = persistedStageEvents(latestStage, executionTrace);
  const renderableMessages = messages.filter((message) => (
    message.role !== 'assistant'
    || !NON_ANSWER_ASSISTANT_MESSAGES.has((message.content || '').trim())
  ));
  const rawAssistantText = (assistantText || '').trim();
  const normalizedAssistantText = sanitizeAssistantTerminalDiagnostics(rawAssistantText);
  const traceAssistantId = rawAssistantText
    ? [...renderableMessages].reverse().find((message) => (
        message.role === 'assistant'
        && ((message.content || '').trim() === rawAssistantText
          || sanitizeAssistantTerminalDiagnostics((message.content || '').trim()) === normalizedAssistantText)
      ))?.id
    : undefined;
  const historicalTraceRecords = (executionTraceHistory || []).filter((record) => (
    Boolean(record?.runId && record.executionTrace)
  ));
  const traceRecordByMessageId = new Map<string, AgentExecutionTraceRecord>();
  const assistantMessages = renderableMessages.filter((message) => (
    message.role === 'assistant' && (message.content || '').trim().length > 0
  ));
  const usedTraceRunIds = new Set<string>();
  const matchesTraceAnswer = (messageContent: string, finalText: string): boolean => {
    const messageAnswer = normalizeAnswerForTraceMatch(messageContent);
    const traceAnswer = normalizeAnswerForTraceMatch(finalText);
    return Boolean(traceAnswer && messageAnswer && messageAnswer === traceAnswer);
  };
  assistantMessages.forEach((message) => {
    const record = historicalTraceRecords.find((candidate) => (
      !usedTraceRunIds.has(candidate.runId)
      && matchesTraceAnswer(message.content || '', candidate.finalText || '')
    ));
    if (record) {
      usedTraceRunIds.add(record.runId);
      traceRecordByMessageId.set(message.id, record);
    }
  });
  const runtimeMessages = renderableMessages
    .filter((message) => (message.content || '').trim().length > 0)
    .map((message) => {
      const traceRecord = traceRecordByMessageId.get(message.id);
      const startedAt = traceRecord?.createdAt ? Date.parse(traceRecord.createdAt) : Number.NaN;
      const finishedAt = traceRecord?.updatedAt ? Date.parse(traceRecord.updatedAt) : Number.NaN;
      const runDurationMs = Number.isFinite(startedAt) && Number.isFinite(finishedAt) && finishedAt >= startedAt
        ? finishedAt - startedAt
        : undefined;
      const messageExecutionTrace = traceRecord?.executionTrace
        ?? (message.id === traceAssistantId ? executionTrace : undefined);
      const stages = traceRecord
        ? persistedStageEvents(traceRecord.latestStage, traceRecord.executionTrace)
        : message.id === traceAssistantId
          ? persistedStages
          : [];
      const messageAnswer = message.role === 'assistant'
        ? sanitizeAssistantTerminalDiagnostics(message.content || '')
        : '';
      const displayParts = messageExecutionTrace
        ? displayPartsForRuntime(
            messageExecutionTrace,
            message.id === traceAssistantId ? normalizedAssistantText : messageAnswer,
          )
        : [];
      return {
        id: message.id,
        role: normalizeMessageRole(message.role),
        createdAt: message.createdAt ? new Date(message.createdAt) : new Date(),
        content: displayParts.length > 0
          ? displayParts
          : [{
              type: 'text' as const,
              text: message.role === 'assistant'
                ? sanitizeAssistantTerminalDiagnostics(message.content || '')
                : message.content || '',
            }],
        ...(
          (stages.length > 0 || messageExecutionTrace)
            ? {
                metadata: {
                  ...(stages.length > 0 ? { unstable_data: stages } : {}),
                  ...(messageExecutionTrace
                    ? { custom: {
                        agent_execution_trace: messageExecutionTrace,
                        ...(runDurationMs !== undefined ? { agent_run_duration_ms: runDurationMs } : {}),
                      } }
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
  const executionTrace = detail.executionTrace;
  const executionTraceHistory = detail.executionTraces || [];
  const lastHistoricalTrace = executionTraceHistory.at(-1);
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
    executionTraceHistory.length,
    executionTraceHistory.map((record) => `${record.runId}:${record.updatedAt ?? ''}`).join(','),
    lastHistoricalTrace?.executionTrace?.stages?.length ?? 0,
    lastHistoricalTrace?.executionTrace?.displayParts?.length ?? 0,
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
