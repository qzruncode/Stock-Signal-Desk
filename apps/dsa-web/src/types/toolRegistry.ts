export type ToolCategory =
  | 'data'
  | 'market'
  | 'financials'
  | 'sentiment'
  | 'macro'
  | 'search'
  | 'analysis';

export interface ToolParameterSpec {
  name: string;
  type: string;
  description?: string | null;
  required: boolean;
  enum?: unknown[] | null;
  default?: unknown;
}

export interface ToolMeta {
  name: string;
  category: ToolCategory;
  description: string;
  parameters: ToolParameterSpec[];
}

export interface ToolRegistryResponse {
  total: number;
  categories: Record<string, number>;
  tools: ToolMeta[];
}

/** POST /api/v1/agent/tool-registry/execute 响应(单个工具试运行结果)。 */
export interface ToolExecuteResult {
  toolName: string;
  arguments: Record<string, unknown>;
  success: boolean;
  result?: unknown;
  error?: string;
  durationMs: number;
}