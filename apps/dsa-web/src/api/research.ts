import apiClient from './index';

export interface ResearchNote {
  id: string;
  symbol: string;
  verdict: string;
  run_id: string;
  as_of_at: string;
  baseline_trade_date: string | null;
  baseline_price: number | null;
  thesis: { blocks?: { content: string; kind: string }[] };
  evidence: { items?: { source_refs?: string[]; data_time?: string }[] };
  lifecycle_status: string;
  outcomes: {
    horizon_trading_days: number;
    status: string;
    return_pct: number | null;
    max_adverse_excursion_pct: number | null;
  }[];
}
export interface AlertParameters {
  kinds: ('news' | 'financial' | 'research')[];
  interval_seconds: number;
  cooldown_seconds: number;
  below_price: number | null;
}
export interface ResearchAlertInput {
  name: string;
  target_scope: 'single_symbol' | 'watchlist_group';
  target: string;
  enabled: boolean;
  notification_enabled: boolean;
  parameters: AlertParameters;
}
export interface ResearchAlert extends ResearchAlertInput {
  id: number;
  next_check_at: string | null;
  state: { checked_at?: string; error?: string };
}
export interface AlertHistory {
  id: number;
  name: string;
  status: string;
  created_at: string;
  changes: { symbol: string; kind: string; before: unknown; after: unknown }[];
}
export const researchApi = {
  async notes(symbol?: string, signal?: AbortSignal) {
    return (
      await apiClient.get<{ items: ResearchNote[] }>('/api/v1/agent/financial-conclusions', {
        params: { symbol: symbol || undefined },
        signal,
      })
    ).data.items;
  },
  async refresh() {
    return (await apiClient.post('/api/v1/agent/financial-conclusions/refresh')).data;
  },
  async alerts(signal?: AbortSignal) {
    return (await apiClient.get<{ items: ResearchAlert[] }>('/api/v1/agent/research-alerts', { signal })).data.items;
  },
  async history(signal?: AbortSignal) {
    return (await apiClient.get<{ items: AlertHistory[] }>('/api/v1/agent/research-alerts/history', { signal })).data
      .items;
  },
  async saveAlert(input: ResearchAlertInput, id?: number) {
    const { name, target_scope, target, enabled, notification_enabled, parameters } = input;
    const payload = { name, target_scope, target, enabled, notification_enabled, parameters };
    return (
      await (id
        ? apiClient.put(`/api/v1/agent/research-alerts/${id}`, payload)
        : apiClient.post('/api/v1/agent/research-alerts', payload))
    ).data as ResearchAlert;
  },
  async checkAlert(id: number) {
    return (await apiClient.post(`/api/v1/agent/research-alerts/${id}/check`)).data as { status: string };
  },
};
