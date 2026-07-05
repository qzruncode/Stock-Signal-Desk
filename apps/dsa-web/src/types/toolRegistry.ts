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