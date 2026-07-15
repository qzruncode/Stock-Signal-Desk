import apiClient from './index';
import { toCamelCase } from './utils';
import type { ToolCategory, ToolExecuteResult, ToolRegistryResponse } from '../types/toolRegistry';

export const toolRegistryApi = {
  async listTools(category?: ToolCategory): Promise<ToolRegistryResponse> {
    const params = category ? { category } : undefined;
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/agent/tool-registry', {
      params,
    });
    return toCamelCase<ToolRegistryResponse>(response.data);
  },

  /** 试运行单个工具。 */
  async runTool(
    toolName: string,
    args: Record<string, unknown>,
  ): Promise<ToolExecuteResult> {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/agent/tool-registry/execute',
      { tool_name: toolName, arguments: args },
      { timeout: 120000 },
    );
    return toCamelCase<ToolExecuteResult>(response.data);
  },
};
