import apiClient from "./index";
import { normalizeConversationDetail } from "./agent/projection";
import { toCamelCase } from "./utils";
import type { AgentCheckpointHistoryResponse, AgentCheckpointSummary, AgentInterruptDecision, ChatConversationDetail, ChatConversationItem, ChatConversationListResponse } from "./agent/types";

export * from "./agent/types";

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

  async getConversation(conversationId: string, signal?: AbortSignal): Promise<ChatConversationDetail> {
    const response = await apiClient.get<Record<string, unknown>>(`/api/v1/agent/conversations/${conversationId}`, { signal });
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
      decision: AgentInterruptDecision['decision'];
      message?: string;
    },
  ): Promise<void> {
    await apiClient.post(
      `/api/v1/agent/conversations/${conversationId}/interrupts/${interruptId}/decision`,
      {
        run_id: payload.runId,
        fingerprint: payload.fingerprint,
        decision: payload.decision,
        ...(payload.message ? { message: payload.message } : {}),
      },
    );
  },

};
