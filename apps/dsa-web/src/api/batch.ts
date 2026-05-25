import apiClient from './index';

export interface BatchRunItem {
  id: number;
  run_id: string;
  triggered_by: string;
  template_id: string | null;
  template_name: string | null;
  stock_count: number;
  success_count: number;
  fail_count: number;
  started_at: string | null;
  completed_at: string | null;
  report_path: string | null;
  results_json: string | null;
  stock_codes_json: string | null;
  status: string;
}

export interface BatchRunListResponse {
  runs: BatchRunItem[];
}

export interface BatchRunTriggerRequest {
  stock_codes: string[];
  template_id: string;
}

export interface BatchRunProgress {
  running: boolean;
  state: {
    run_id?: string;
    total?: number;
    completed?: number;
    success?: number;
    failed?: number;
    current_stock?: string;
    current_message?: string;
    active_stocks?: string[];
    status?: string;
    paused?: boolean;
    stopping?: boolean;
    [key: string]: unknown;
  } | null;
}

export interface BatchSchedule {
  id: number;
  enabled: boolean;
  times: string[];
  template_id: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export const batchApi = {
  async triggerRun(data: BatchRunTriggerRequest): Promise<{ message: string; stock_count: number; template_name: string }> {
    const response = await apiClient.post('/api/v1/batch/run', data, {
      validateStatus: (status) => status === 202,
    });
    return response.data;
  },

  async resumeRun(runId: string, stockCodes: string[]): Promise<{ message: string; stock_count: number; pending_count: number; template_name: string }> {
    const response = await apiClient.post(`/api/v1/batch/runs/${runId}/resume`, {
      stock_codes: stockCodes,
    }, {
      validateStatus: (status) => status === 202,
    });
    return response.data;
  },

  async pauseCurrentRun(): Promise<{ message: string }> {
    const response = await apiClient.post('/api/v1/batch/runs/current/pause');
    return response.data;
  },

  async resumeCurrentRun(): Promise<{ message: string }> {
    const response = await apiClient.post('/api/v1/batch/runs/current/resume');
    return response.data;
  },

  async stopCurrentRun(): Promise<{ message: string }> {
    const response = await apiClient.post('/api/v1/batch/runs/current/stop');
    return response.data;
  },

  async getRuns(limit = 20): Promise<BatchRunItem[]> {
    const response = await apiClient.get<BatchRunListResponse>('/api/v1/batch/runs', { params: { limit } });
    return response.data.runs;
  },

  async getRunDetail(runId: string): Promise<BatchRunItem> {
    const response = await apiClient.get<BatchRunItem>(`/api/v1/batch/runs/${runId}`);
    return response.data;
  },

  async getRunReport(runId: string): Promise<string> {
    const response = await apiClient.get<string>(`/api/v1/batch/runs/${runId}/report.md`, {
      responseType: 'text',
    });
    return response.data;
  },

  async deleteRun(runId: string): Promise<void> {
    await apiClient.delete(`/api/v1/batch/runs/${runId}`);
  },

  async getCurrentProgress(): Promise<BatchRunProgress> {
    const response = await apiClient.get<BatchRunProgress>('/api/v1/batch/runs/current');
    return response.data;
  },

  async getSchedule(): Promise<BatchSchedule> {
    const response = await apiClient.get<BatchSchedule>('/api/v1/batch/schedule');
    return response.data;
  },

  async updateSchedule(data: { enabled: boolean; times: string[]; template_id: string }): Promise<BatchSchedule> {
    const response = await apiClient.put<BatchSchedule>('/api/v1/batch/schedule', data);
    return response.data;
  },
};
