import apiClient from './index';

export type DatasetId = 'securities' | 'calendar' | 'kline' | 'financials' | 'quotes' | 'news' | 'announcements' | 'market' | 'macro' | 'rss';
export type Freshness = 'fresh' | 'stale' | 'missing' | 'failed' | 'partial' | 'unknown';
export interface SyncPolicy { enabled: boolean; interval_seconds: number; max_age_seconds: number; next_run_at?: string | null }
export interface SyncJob {
  id: string; dataset: DatasetId; status: 'queued' | 'running' | 'success' | 'partial' | 'failed' | 'cancelled';
  trigger: string; mode: string; total: number; progress: number; succeeded: number; failed: number;
  message: string; error: string | null; created_at: string; started_at: string | null; finished_at: string | null; cancel_requested: boolean;
}
export interface DatasetSummary extends Record<Freshness, number> {
  id: DatasetId; label: string; scope: 'market' | 'subscribed'; total: number; coverage_percent: number | null;
  latest_data_time: string | null; oldest_data_time: string | null; policy: SyncPolicy; latest_job: SyncJob | null;
}
interface ServiceHealth {
  status: string; database: string; provider: string; checked_at: string;
  legacy_import_issues?: number;
  components: Record<string, { healthy: boolean; last_seen_at: string; detail: string }>;
}
export interface DatasetOverview { items: DatasetSummary[]; checked_at: string; service: ServiceHealth; event_cursor: string }
export interface DataServiceChange {
  datasets: DatasetSummary[]; jobs: SyncJob[]; coverage: Partial<Record<DatasetId, string[]>>;
  checked_at: string; service: ServiceHealth;
}
export interface CoverageRow {
  symbol: string; name: string; status: Freshness; data_time: string | null;
  checked_at: string | null; last_success_at: string | null; source: string | null; version: string | null; error: string | null;
}
export interface JobDetail extends SyncJob {
  items: { symbol: string; status: string; attempts: number; error: string | null }[];
  items_total: number; page: number;
}
const root = '/api/v1/data-service';

/** A slow HTTP snapshot cannot roll back a newer, already applied stream event. */
export function preserveNewerOverview(previous: unknown, incoming: unknown): DatasetOverview {
  const old = previous as DatasetOverview | undefined;
  const next = incoming as DatasetOverview;
  if (old?.event_cursor && next.event_cursor) {
    const [oldTime, oldSequence] = old.event_cursor.split('-').map(BigInt);
    const [nextTime, nextSequence] = next.event_cursor.split('-').map(BigInt);
    if (oldTime > nextTime || (oldTime === nextTime && oldSequence > nextSequence)) return old;
  }
  return next;
}

export const dataMaintenanceApi = {
  eventsUrl(after: string): string { return `${apiClient.defaults.baseURL || ''}${root}/events?after=${encodeURIComponent(after)}`; },
  async overview(): Promise<DatasetOverview> { return (await apiClient.get(root + '/datasets')).data; },
  async jobs(dataset?: DatasetId, page = 1): Promise<{ items: SyncJob[]; total: number }> { return (await apiClient.get(root + '/jobs', { params: { dataset, page, limit: 20 } })).data; },
  async coverage(dataset: DatasetId, params: { page: number; status: string; search: string }): Promise<{ items: CoverageRow[]; total: number }> {
    return (await apiClient.get(root + '/datasets/' + dataset + '/coverage', { params: { ...params, page_size: 20 } })).data;
  },
  async policy(dataset: DatasetId, policy: SyncPolicy): Promise<SyncPolicy> {
    const { enabled, interval_seconds, max_age_seconds } = policy;
    return (await apiClient.put(root + '/datasets/' + dataset + '/policy', { enabled, interval_seconds, max_age_seconds })).data;
  },
  async sync(dataset: DatasetId, mode: 'stale' | 'missing' | 'all', symbols: string[] = []): Promise<SyncJob> {
    return (await apiClient.post(root + '/jobs', { dataset, mode, symbols })).data;
  },
  async job(id: string, page: number, failures_only: boolean): Promise<JobDetail> {
    return (await apiClient.get(root + '/jobs/' + id, { params: { page, page_size: 20, failures_only } })).data;
  },
  async cancel(id: string): Promise<SyncJob> { return (await apiClient.post(root + '/jobs/' + id + '/cancel')).data; },
  async retry(id: string): Promise<SyncJob> { return (await apiClient.post(root + '/jobs/' + id + '/retry')).data; },
  async exportCoverage(dataset: DatasetId): Promise<void> {
    const response = await apiClient.get(root + '/datasets/' + dataset + '/coverage.csv', { responseType: 'blob' });
    const url = URL.createObjectURL(response.data);
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = dataset + '-coverage.csv';
    document.body.appendChild(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};
