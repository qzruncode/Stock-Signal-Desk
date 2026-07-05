import apiClient from './index';
import { toCamelCase } from './utils';
import type { ToolCategory, ToolRegistryResponse } from '../types/toolRegistry';

export const toolRegistryApi = {
  async listTools(category?: ToolCategory): Promise<ToolRegistryResponse> {
    const params = category ? { category } : undefined;
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/agent/tool-registry', {
      params,
    });
    return toCamelCase<ToolRegistryResponse>(response.data);
  },
};