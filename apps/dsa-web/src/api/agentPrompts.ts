import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  ActiveAgentPromptResponse,
  AgentPromptListResponse,
  AgentPromptTemplate,
  CreateAgentPromptRequest,
  UpdateAgentPromptRequest,
} from '../types/agentPrompts';

const toTemplate = (data: unknown): AgentPromptTemplate =>
  toCamelCase<AgentPromptTemplate>(data);

export const agentPromptsApi = {
  async listPrompts(): Promise<AgentPromptTemplate[]> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/agent/prompts');
    const data = toCamelCase<AgentPromptListResponse>(response.data);
    return (data.templates || []).map(toTemplate);
  },

  async getActivePrompt(): Promise<ActiveAgentPromptResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/agent/prompts/active');
    return toCamelCase<ActiveAgentPromptResponse>(response.data);
  },

  async createPrompt(payload: CreateAgentPromptRequest): Promise<AgentPromptTemplate> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/agent/prompts', {
      name: payload.name,
      content: payload.content,
      is_active: payload.isActive ?? false,
    });
    return toTemplate(response.data);
  },

  async updatePrompt(
    templateId: number,
    payload: UpdateAgentPromptRequest,
  ): Promise<AgentPromptTemplate> {
    const body: Record<string, unknown> = {};
    if (payload.name !== undefined) body.name = payload.name;
    if (payload.content !== undefined) body.content = payload.content;
    const response = await apiClient.put<Record<string, unknown>>(
      `/api/v1/agent/prompts/${templateId}`,
      body,
    );
    return toTemplate(response.data);
  },

  async deletePrompt(templateId: number): Promise<void> {
    await apiClient.delete(`/api/v1/agent/prompts/${templateId}`);
  },

  async activatePrompt(templateId: number): Promise<AgentPromptTemplate> {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/agent/prompts/${templateId}/activate`,
    );
    return toTemplate(response.data);
  },
};
