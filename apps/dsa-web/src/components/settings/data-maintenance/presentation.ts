import type { DatasetId, SyncJob } from '../../../api/dataMaintenance';

export const liveQuery = { staleTime: Infinity, refetchOnWindowFocus: false } as const;
export const datasetNames: Record<DatasetId, string> = {
  securities: '证券主数据', calendar: '交易日历', kline: '日 K 线', financials: '财务报表',
  quotes: '实时行情', news: '公司新闻', announcements: '公司公告', market: '市场与资金流', macro: '指数与宏观', rss: '资讯订阅',
};
export const freshnessNames = {
  fresh: '已更新', stale: '已过期', missing: '待采集', failed: '采集失败', partial: '数据不完整', unknown: '时效待确认',
};
export const active = (job?: SyncJob | null) => !!job && ['queued', 'running'].includes(job.status);
export const retryable = (job: SyncJob) => ['failed', 'partial', 'cancelled'].includes(job.status);
export const jobMessage = (job?: SyncJob | null) => job?.status === 'cancelled' ? '任务已取消，已保存的数据会保留。' : job?.error || job?.message;
export const statusLabel = (job?: SyncJob | null) => !job ? '暂无近期记录' : ({
  queued: '排队中', running: '执行中', success: '已完成', partial: '部分完成', failed: '失败', cancelled: '已取消',
})[job.status];
export const statusVariant = (job?: SyncJob | null): 'default' | 'success' | 'warning' | 'danger' =>
  job?.status === 'success' ? 'success' : job?.status === 'failed' ? 'danger' :
    active(job) || job?.status === 'partial' ? 'warning' : 'default';
export const formatDate = (value?: string | null) => {
  if (!value) return '暂无记录';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};
export const messageOf = (error: unknown) => {
  const value = error as { response?: { data?: { detail?: unknown } }; message?: string };
  return typeof value.response?.data?.detail === 'string' ? value.response.data.detail : value.message || '操作失败，请稍后重试';
};
