import apiClient from './index';
import { toCamelCase } from './utils';

export interface ChatConversationItem {
  id: string;
  title: string;
  titleSource: 'auto' | 'manual' | string;
  previewText?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ChatConversationMessage {
  id: string;
  conversationId: string;
  role: 'user' | 'assistant' | 'system' | string;
  content: string;
  sequence: number;
  createdAt: string;
}

export interface ChatConversationThreadStateMessage {
  message: Record<string, unknown>;
  parentId: string | null;
  runConfig?: Record<string, unknown>;
}

export interface ChatConversationThreadState {
  headId?: string | null;
  messages: ChatConversationThreadStateMessage[];
}

export interface PersistedAgentStage {
  event: 'agent_stage' | 'agent_stage_v2';
  runId?: string;
  run_id?: string;
  stage: string;
  status: 'started' | 'completed' | 'succeeded' | 'failed' | 'blocked' | 'cancelled';
  taskId?: string | null;
  task_id?: string | null;
  actionId?: string | null;
  action_id?: string | null;
  toolCallId?: string | null;
  tool_call_id?: string | null;
  roundId?: string | null;
  round_id?: string | null;
  errorCode?: string | null;
  error_code?: string | null;
  summary?: string;
  occurredAt?: string;
  occurred_at?: string;
  details?: Record<string, unknown>;
}

export interface AgentExecutionTrace {
  /** Ordered, bounded assistant-stream parts used for terminal replay. */
  displayParts?: Record<string, unknown>[];
  stages?: PersistedAgentStage[];
  actions?: Record<string, unknown>[];
  toolResults?: Record<string, unknown>[];
  evidence?: Record<string, unknown>[];
  claimEvidence?: Record<string, unknown>[];
  loop?: Record<string, unknown>;
  completedToolCallIds?: string[];
  /** Defensive marker when a legacy or malformed API response was compacted for rendering. */
  clientTraceTruncated?: boolean;
}

export interface AgentCheckpointTaskSummary {
  id?: string | null;
  name?: string | null;
  interruptCount: number;
  hasError: boolean;
  errorType?: string | null;
}

export interface AgentCheckpointMetadata {
  source?: string | null;
  step?: number | null;
  writeKeys: string[];
}

export interface AgentCheckpointStateSummary {
  runId?: string | null;
  conversationId?: string | null;
  status?: string | null;
  messageCount: number;
  toolResultCount: number;
  evidenceCount: number;
  modelTurnCount: number;
  toolCallCount: number;
  evidenceRepairCount: number;
  hasPendingInterrupt: boolean;
  hasAnswer: boolean;
}

/** Bounded, read-only projection of a native LangGraph checkpoint. */
export interface AgentCheckpointSummary {
  checkpointId?: string | null;
  parentCheckpointId?: string | null;
  createdAt?: string | null;
  metadata: AgentCheckpointMetadata;
  next: string[];
  tasks: AgentCheckpointTaskSummary[];
  state: AgentCheckpointStateSummary;
}

export interface AgentCheckpointHistoryResponse {
  conversationId: string;
  threadId: string;
  currentCheckpointId?: string | null;
  checkpointAuthority: string;
  runLifecycleAuthority: string;
  readOnly: boolean;
  items: AgentCheckpointSummary[];
  hasMore: boolean;
  nextBeforeCheckpointId?: string | null;
}

export interface PendingAgentInterrupt {
  interruptId: string;
  runId: string;
  conversationId?: string;
  fingerprint: string;
  actionId?: string;
  toolName: string;
  summary: string;
  arguments: Record<string, unknown>;
  createdAt?: string;
}

export interface ChatConversationDetail extends ChatConversationItem {
  messages: ChatConversationMessage[];
  threadState?: ChatConversationThreadState | null;
  executionTrace?: AgentExecutionTrace | null;
  /** 后端是否仍在生成该对话的回复(刷新后前端据此判断是否续流)。 */
  isGenerating?: boolean;
  resumeState?: {
    runId?: string | null;
    active: boolean;
    isGenerating?: boolean;
    status?: 'running' | 'completed' | 'partial' | 'failed' | 'cancelled' | string | null;
    afterChunkIndex: number;
    eventCursor?: number;
    assistantText: string;
    hasToolEvents?: boolean;
    latestStage?: PersistedAgentStage | null;
    /** @deprecated The canonical trace is the conversation-level executionTrace. */
    executionTrace?: AgentExecutionTrace | null;
    pendingInterrupt?: PendingAgentInterrupt | null;
  };
  pendingInterrupt?: PendingAgentInterrupt | null;
}

// Conversation detail is a rendering boundary, not an audit export.  The
// server emits a compact execution trace for normal LangGraph runs, but a
// rolling deployment or an old stored record can still return a raw trace.
// Project it before camelcase-keys walks the payload so one oversized tool
// result cannot freeze the whole chat page during hydration.
const CLIENT_TRACE_MAX_CHARACTERS = 180_000;
const CLIENT_TRACE_MAX_DEPTH = 7;
const CLIENT_TRACE_MAX_OBJECT_KEYS = 24;
const CLIENT_TRACE_MAX_TEXT = 1_600;
const CLIENT_DISPLAY_PART_TEXT = 12_000;
const CLIENT_TRACE_FIELD_LIMITS: Array<[string, string, number]> = [
  ['display_parts', 'displayParts', 240],
  ['stages', 'stages', 120],
  ['actions', 'actions', 32],
  ['tool_results', 'toolResults', 80],
  ['evidence', 'evidence', 80],
  ['claim_evidence', 'claimEvidence', 80],
  ['loop', 'loop', 1],
  ['completed_tool_call_ids', 'completedToolCallIds', 80],
];

type TraceBudget = { remaining: number; exhausted: boolean };

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

const projectTraceValue = (
  value: unknown,
  budget: TraceBudget,
  depth = 0,
  arrayLimit = 16,
  textLimit = CLIENT_TRACE_MAX_TEXT,
): unknown => {
  if (budget.remaining <= 0) {
    budget.exhausted = true;
    return '[执行详情已折叠]';
  }
  if (value === null || typeof value === 'boolean' || typeof value === 'number') {
    budget.remaining -= 24;
    return value;
  }
  if (typeof value === 'string') {
    const projected = value.slice(0, Math.min(textLimit, budget.remaining));
    budget.remaining -= projected.length;
    if (projected.length < value.length) budget.exhausted = true;
    return projected;
  }
  if (depth >= CLIENT_TRACE_MAX_DEPTH) {
    budget.remaining -= 16;
    budget.exhausted = true;
    return '[嵌套详情已折叠]';
  }
  if (Array.isArray(value)) {
    const projected = value.slice(0, arrayLimit).map((item) => (
      projectTraceValue(item, budget, depth + 1, 16, textLimit)
    ));
    if (value.length > projected.length) {
      projected.push(`[其余 ${value.length - projected.length} 项已折叠]`);
      budget.exhausted = true;
    }
    return projected;
  }
  if (isRecord(value)) {
    const projected: Record<string, unknown> = {};
    let keyCount = 0;
    for (const key in value) {
      if (!Object.prototype.hasOwnProperty.call(value, key)) continue;
      if (keyCount >= CLIENT_TRACE_MAX_OBJECT_KEYS || budget.remaining <= 0) {
        projected._clientTruncated = true;
        budget.exhausted = true;
        break;
      }
      const safeKey = key.slice(0, 96);
      budget.remaining -= safeKey.length;
      projected[safeKey] = projectTraceValue(value[key], budget, depth + 1, 16, textLimit);
      keyCount += 1;
    }
    return projected;
  }
  const projected = String(value).slice(0, Math.min(textLimit, budget.remaining));
  budget.remaining -= projected.length;
  return projected;
};

const projectExecutionTraceForClient = (value: unknown): Record<string, unknown> | undefined => {
  if (!isRecord(value)) return undefined;
  const budget: TraceBudget = { remaining: CLIENT_TRACE_MAX_CHARACTERS, exhausted: false };
  const projected: Record<string, unknown> = {};
  for (const [snakeKey, camelKey, arrayLimit] of CLIENT_TRACE_FIELD_LIMITS) {
    const raw = value[snakeKey] ?? value[camelKey];
    if (raw === undefined) continue;
    projected[snakeKey] = projectTraceValue(
      raw,
      budget,
      0,
      arrayLimit,
      snakeKey === 'display_parts' ? CLIENT_DISPLAY_PART_TEXT : CLIENT_TRACE_MAX_TEXT,
    );
  }
  if (budget.exhausted) projected.client_trace_truncated = true;
  return projected;
};

/**
 * The active conversation contract deliberately excludes the obsolete
 * assistant-ui `thread_state` snapshot. It may contain unbounded historical
 * tool payloads; canonical messages plus the LangGraph trace are the only
 * rendering inputs. Ignore it defensively during a rolling server upgrade.
 */
const normalizeConversationDetail = (payload: Record<string, unknown>): ChatConversationDetail => {
  const presentationPayload: Record<string, unknown> = { ...payload };
  delete presentationPayload.thread_state;
  delete presentationPayload.threadState;
  const rawExecutionTrace = presentationPayload.execution_trace ?? presentationPayload.executionTrace;
  delete presentationPayload.execution_trace;
  delete presentationPayload.executionTrace;
  const rawResumeState = presentationPayload.resume_state ?? presentationPayload.resumeState;
  const resumeStatePayload = isRecord(rawResumeState) ? { ...rawResumeState } : undefined;
  const rawResumeTrace = resumeStatePayload?.execution_trace ?? resumeStatePayload?.executionTrace;
  if (resumeStatePayload) {
    delete resumeStatePayload.execution_trace;
    delete resumeStatePayload.executionTrace;
    presentationPayload.resume_state = resumeStatePayload;
    delete presentationPayload.resumeState;
  }
  const executionTrace = projectExecutionTraceForClient(rawExecutionTrace ?? rawResumeTrace);
  if (executionTrace) presentationPayload.execution_trace = executionTrace;
  const rawPending = presentationPayload.pending_interrupt ?? presentationPayload.pendingInterrupt;
  const data = toCamelCase<ChatConversationDetail>(presentationPayload);
  const normalizedPending = rawPending && typeof rawPending === 'object' && !Array.isArray(rawPending)
    ? {
        ...toCamelCase<PendingAgentInterrupt>(rawPending as Record<string, unknown>),
        arguments: (
          (rawPending as Record<string, unknown>).arguments
          && typeof (rawPending as Record<string, unknown>).arguments === 'object'
          && !Array.isArray((rawPending as Record<string, unknown>).arguments)
        )
          ? (rawPending as Record<string, unknown>).arguments as Record<string, unknown>
          : {},
      }
    : null;
  return {
    ...data,
    pendingInterrupt: normalizedPending,
    ...(data.resumeState
      ? {
          resumeState: {
            ...data.resumeState,
            pendingInterrupt: normalizedPending,
          },
        }
      : {}),
    messages: (data.messages || []).map((message) => toCamelCase<ChatConversationMessage>(message)),
  };
};

export interface ChatConversationListResponse {
  items: ChatConversationItem[];
  total: number;
  page: number;
  limit: number;
}

export const agentApi = {
  async listConversations(): Promise<ChatConversationListResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/agent/conversations');
    const data = toCamelCase<ChatConversationListResponse>(response.data);
    return {
      ...data,
      items: (data.items || []).map((item) => toCamelCase<ChatConversationItem>(item)),
    };
  },

  async createConversation(): Promise<ChatConversationItem> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/agent/conversations');
    return toCamelCase<ChatConversationItem>(response.data);
  },

  async getConversation(conversationId: string): Promise<ChatConversationDetail> {
    const response = await apiClient.get<Record<string, unknown>>(`/api/v1/agent/conversations/${conversationId}`);
    return normalizeConversationDetail(response.data);
  },

  async getConversationCheckpoints(
    conversationId: string,
    params: { limit?: number; beforeCheckpointId?: string | null } = {},
  ): Promise<AgentCheckpointHistoryResponse> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/agent/conversations/${conversationId}/checkpoints`,
      {
        params: {
          limit: params.limit,
          before_checkpoint_id: params.beforeCheckpointId || undefined,
        },
      },
    );
    const data = toCamelCase<AgentCheckpointHistoryResponse>(response.data);
    return {
      ...data,
      items: (data.items || []).map((item) => toCamelCase<AgentCheckpointSummary>(item)),
    };
  },

  async syncConversationSnapshot(
    conversationId: string,
    payload: {
      messages?: Array<Record<string, unknown>>;
      pruneAgentContextToMessages?: boolean;
    },
  ): Promise<ChatConversationDetail> {
    // LangGraph persists the orchestration state itself.  A thread runtime
    // export is only an opaque renderer cache and can contain complete raw
    // tool payloads, so never send it back to the server during an edit.
    const requestBody: Record<string, unknown> = {};
    if (payload.messages !== undefined) {
      requestBody.messages = payload.messages;
    }
    if (payload.pruneAgentContextToMessages === true) {
      requestBody.prune_agent_context_to_messages = true;
    }
    const response = await apiClient.put<Record<string, unknown>>(
      `/api/v1/agent/conversations/${conversationId}/snapshot`,
      requestBody,
    );
    return normalizeConversationDetail(response.data);
  },

  async renameConversation(conversationId: string, title: string): Promise<ChatConversationItem> {
    const response = await apiClient.patch<Record<string, unknown>>(`/api/v1/agent/conversations/${conversationId}`, {
      title,
    });
    return toCamelCase<ChatConversationItem>(response.data);
  },

  async deleteConversation(conversationId: string): Promise<void> {
    await apiClient.delete(`/api/v1/agent/conversations/${conversationId}`);
  },

  async clearAllConversations(): Promise<number> {
    const response = await apiClient.delete<{ deleted?: number }>('/api/v1/agent/conversations');
    return Number(response.data.deleted || 0);
  },

  async cancelConversationRun(conversationId: string): Promise<boolean> {
    const response = await apiClient.post<{ cancelled: boolean }>(
      `/api/v1/agent/conversations/${conversationId}/cancel`,
    );
    return response.data.cancelled === true;
  },

  async decideInterrupt(
    conversationId: string,
    interruptId: string,
    payload: {
      runId: string;
      fingerprint: string;
      decision: 'approve' | 'reject';
    },
  ): Promise<void> {
    await apiClient.post(
      `/api/v1/agent/conversations/${conversationId}/interrupts/${interruptId}/decision`,
      {
        run_id: payload.runId,
        fingerprint: payload.fingerprint,
        decision: payload.decision,
      },
    );
  },

};
