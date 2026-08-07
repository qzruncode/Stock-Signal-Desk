export type ToolCategory =
  | 'data'
  | 'market'
  | 'financials'
  | 'sentiment'
  | 'macro'
  | 'search'
  | 'analysis'
  | 'research'
  | 'regulatory'
  | 'events'
  | 'risk'
  | 'action';

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
  retrievalDescription: string;
  effect: 'read' | 'side_effect';
  effectMode: 'fixed' | 'argument_dependent';
  approvalPolicy: 'required_for_side_effect';
  timeoutSeconds?: number | null;
  maxAttempts: number;
  retryBackoffSeconds: number;
  idempotent: boolean;
  sensitiveFields: string[];
  parameters: ToolParameterSpec[];
}

export interface ToolRegistryResponse {
  total: number;
  categories: Record<string, number>;
  tools: ToolMeta[];
}
