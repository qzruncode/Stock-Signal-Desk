import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { dataMaintenanceApi as api, type DatasetId, type DatasetOverview, type JobDetail, type SyncJob } from '../../../api/dataMaintenance';

type JobAction = { kind: 'sync'; dataset: DatasetId; mode: 'stale' | 'missing' } | { kind: 'cancel' | 'retry'; job: SyncJob };
type JobPage = { items: SyncJob[]; total: number };

/** Mutations and pushed changes share the existing query keys and durable job IDs. */
function acceptJob(cache: QueryClient, job: SyncJob, isNew: boolean) {
  cache.setQueryData<DatasetOverview>(['data-service', 'overview'], previous => previous ? {
    ...previous, items: previous.items.map(item => {
      if (item.id !== job.dataset) return item;
      const latest = item.latest_job;
      if (latest?.id !== job.id && (!isNew || (latest && Date.parse(latest.created_at) > Date.parse(job.created_at)))) return item;
      return { ...item, latest_job: job };
    }),
  } : previous);
  cache.setQueriesData<JobPage>({ queryKey: ['data-service', 'jobs'] }, previous => previous ? {
    ...previous, items: previous.items.map(item => item.id === job.id ? job : item),
  } : previous);
  cache.setQueriesData<JobDetail>({ queryKey: ['data-service', 'job', job.id] }, previous => previous ? { ...previous, ...job } : previous);
  if (isNew) void cache.invalidateQueries({ predicate: query => query.queryKey[0] === 'data-service' && query.queryKey[1] === 'jobs' &&
    (query.queryKey[2] === 'all' || query.queryKey[2] === job.dataset) });
  void cache.invalidateQueries({ queryKey: ['data-service', 'job', job.id] });
}

export function useMaintenanceJobs() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: (action: JobAction) => action.kind === 'sync' ? api.sync(action.dataset, action.mode) :
      action.kind === 'cancel' ? api.cancel(action.job.id) : api.retry(action.job.id),
    onSuccess: (job, action) => acceptJob(cache, job, action.kind !== 'cancel'),
  });
}
