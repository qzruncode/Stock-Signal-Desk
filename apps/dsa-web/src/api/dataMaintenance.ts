import apiClient from './index';

export type MaintenanceStatus = 'idle' | 'running' | 'syncing_kline' | 'success' | 'failed' | string;

export interface MaintenanceJobStatus {
  status: MaintenanceStatus;
  progress: number;
  total: number;
  kline_progress?: number;
  kline_total?: number;
  started_at: string | null;
  finished_at: string | null;
  message: string;
  error: string | null;
  period?: string | null;
}

export interface KlineIntegrityStatus {
  total_stocks: number;
  stocks_with_kline: number;
  missing: number;
  missing_codes: string[];
  latest_trading_day: string | null;
}

export const dataMaintenanceApi = {
  async syncStockList(): Promise<{ success: boolean; message: string; status: string }> {
    const response = await apiClient.post('/api/v1/stocks/sync/list');
    return response.data;
  },

  async getStockListStatus(): Promise<MaintenanceJobStatus> {
    const response = await apiClient.get<MaintenanceJobStatus>('/api/v1/stocks/sync/list/status');
    return response.data;
  },

  async getKlineIntegrity(): Promise<KlineIntegrityStatus> {
    const response = await apiClient.get<KlineIntegrityStatus>('/api/v1/stocks/kline-status');
    return response.data;
  },

  async syncKline(): Promise<{ success: boolean; message: string; status: string }> {
    const response = await apiClient.post('/api/v1/stocks/sync/kline');
    return response.data;
  },

  async getKlineStatus(): Promise<MaintenanceJobStatus> {
    const response = await apiClient.get<MaintenanceJobStatus>('/api/v1/stocks/sync/kline/status');
    return response.data;
  },

  async syncMissingKline(codes: string[]): Promise<MaintenanceJobStatus> {
    const response = await apiClient.post<MaintenanceJobStatus>('/api/v1/stocks/kline/sync-missing', { codes });
    return response.data;
  },

  async getMissingKlineStatus(): Promise<MaintenanceJobStatus> {
    const response = await apiClient.get<MaintenanceJobStatus>('/api/v1/stocks/kline/sync-missing/status');
    return response.data;
  },

  async syncFinancial(): Promise<{ success: boolean; message: string; status: string; period?: string }> {
    const response = await apiClient.post('/api/v1/stocks/sync/financial');
    return response.data;
  },

  async getFinancialStatus(): Promise<MaintenanceJobStatus> {
    const response = await apiClient.get<MaintenanceJobStatus>('/api/v1/stocks/sync/financial/status');
    return response.data;
  },
};
