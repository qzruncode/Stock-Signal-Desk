import apiClient from './index';

export interface PromptTemplateItem {
  id: string;
  name: string;
  content: string;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface PromptTemplateListResponse {
  templates: PromptTemplateItem[];
}

export interface CreatePromptTemplateRequest {
  name: string;
  content: string;
  is_default?: boolean;
}

export interface UpdatePromptTemplateRequest {
  name?: string;
  content?: string;
  is_default?: boolean;
}

export const promptsApi = {
  async getPromptTemplates(): Promise<PromptTemplateItem[]> {
    const response = await apiClient.get<PromptTemplateListResponse>('/api/v1/prompts');
    return response.data.templates;
  },

  async getPromptTemplate(templateId: string): Promise<PromptTemplateItem> {
    const response = await apiClient.get<PromptTemplateItem>(`/api/v1/prompts/${templateId}`);
    return response.data;
  },

  async createPromptTemplate(data: CreatePromptTemplateRequest): Promise<PromptTemplateItem> {
    const response = await apiClient.post<PromptTemplateItem>('/api/v1/prompts', data);
    return response.data;
  },

  async updatePromptTemplate(templateId: string, data: UpdatePromptTemplateRequest): Promise<PromptTemplateItem> {
    const response = await apiClient.put<PromptTemplateItem>(`/api/v1/prompts/${templateId}`, data);
    return response.data;
  },

  async deletePromptTemplate(templateId: string): Promise<void> {
    await apiClient.delete(`/api/v1/prompts/${templateId}`);
  },
};
