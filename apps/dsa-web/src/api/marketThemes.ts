import apiClient from './index';
import { extractErrorPayloadText } from './error';

export interface MarketStage {
  label: string;
  description: string;
}

export interface MarketMainlineEntry {
  name: string;
  rank: number;
  stage: string;
  reason: string;
  branches: string[];
  focus: string;
  risks: string[];
  evidence: string[];
}

export interface FutureMainlineEntry {
  name: string;
  stage_hint: string;
  reason: string;
  triggers: string[];
}

export interface MarketMainlineReportResponse {
  id?: number;
  report_key?: string;
  mode?: string;
  created_at?: string;
  generated_at: string;
  as_of_date: string;
  overview: string;
  full_report: string;
  market_stage: MarketStage;
  current_mainlines: MarketMainlineEntry[];
  future_mainlines: FutureMainlineEntry[];
  action_summary: string[];
  evidence_digest: {
    policy: string[];
    industry: string[];
    market: string[];
  };
  source_summary?: {
    official_count: number;
    news_count: number;
    report_count: number;
    source_catalog: Array<{
      name: string;
      category: string;
      credibility: string;
      used: boolean;
    }>;
  };
  llm_used: boolean;
  model_used?: string | null;
  raw_stream_output?: string;
  raw_response?: string;
  report_pending?: boolean;
  _cached?: boolean;
  debug_input?: {
    system_prompt?: string;
    user_prompt?: string;
    evidence_pack?: Record<string, unknown>;
  };
}

export interface MarketMainlineReportTaskAccepted {
  task_id: string;
  status: 'pending' | 'processing';
  message?: string;
}

export const marketThemesApi = {
  async getReport(): Promise<MarketMainlineReportResponse> {
    const response = await apiClient.get<MarketMainlineReportResponse>(
      '/api/v1/market/mainline/report',
      { timeout: 10000 },
    );
    return response.data;
  },

  async createReportTask(force: boolean = true): Promise<MarketMainlineReportTaskAccepted> {
    try {
      const response = await apiClient.post<MarketMainlineReportTaskAccepted>(
        '/api/v1/market/mainline/report/tasks',
        null,
        { params: { force }, timeout: 10000 },
      );
      return response.data;
    } catch (error) {
      try {
        const response = await apiClient.get<MarketMainlineReportTaskAccepted>(
          '/api/v1/market/mainline/report/tasks',
          { params: { force }, timeout: 10000 },
        );
        return response.data;
      } catch (fallbackError) {
        const message = extractErrorPayloadText(
          (fallbackError as { response?: { data?: unknown } })?.response?.data,
        );
        const finalError = fallbackError as Error & { message?: string };
        if (message) {
          finalError.message = message;
        } else if ((error as Error)?.message) {
          finalError.message = (error as Error).message;
        }
        throw finalError;
      }
    }
  },
};
