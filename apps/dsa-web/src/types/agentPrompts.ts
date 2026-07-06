/** AI 助手 system prompt 模板相关类型。 */

export interface AgentPromptTemplate {
  id: number;
  name: string;
  content: string;
  isActive: boolean;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface AgentPromptListResponse {
  templates: AgentPromptTemplate[];
}

export interface ActiveAgentPromptResponse {
  content: string;
  isFallback: boolean;
  templateId?: number | null;
  templateName?: string | null;
}

export interface CreateAgentPromptRequest {
  name: string;
  content: string;
  isActive?: boolean;
}

export interface UpdateAgentPromptRequest {
  name?: string;
  content?: string;
}
