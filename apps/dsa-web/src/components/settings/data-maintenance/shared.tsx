import { Badge, Button } from '../../common';
import type { DatasetId, SyncJob } from '../../../api/dataMaintenance';
import { active, retryable, statusLabel, statusVariant } from './presentation';

export function JobStatus({ job }: { job: SyncJob }) {
  return <Badge variant={statusVariant(job)} className="text-[10px]">{job.cancel_requested && active(job) ? '取消中' : statusLabel(job)}</Badge>;
}

export interface JobInteractions {
  busyDatasets?: DatasetId[];
  canStart: boolean;
  canCancel: boolean;
  pending: boolean;
  onDetails?: (job: SyncJob) => void;
  onCancel: (job: SyncJob) => void;
  onRetry: (job: SyncJob) => void;
}

export function JobControls({ job, onDetails, onCancel, onRetry, canStart, canCancel, pending, busyDatasets = [] }: JobInteractions & { job: SyncJob }) {
  return <div className="flex shrink-0 items-center gap-1">
    {onDetails && <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[10px]" onClick={() => onDetails(job)}>详情</Button>}
    {active(job) && <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[10px] text-warning" disabled={!canCancel || pending || job.cancel_requested}
      onClick={() => onCancel(job)}>{job.cancel_requested ? '取消中' : '取消任务'}</Button>}
    {retryable(job) && <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[10px] text-primary" disabled={!canStart || pending || busyDatasets.includes(job.dataset)}
      onClick={() => onRetry(job)}>重试未完成项</Button>}
  </div>;
}

export function PageNavigation({ page, total, pageSize = 20, disabled = false, onChange }: {
  page: number; total: number; pageSize?: number; disabled?: boolean; onChange: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-3 text-xs text-muted-foreground">
    <span>共 {total.toLocaleString()} 条 · 第 {page} 页</span>
    <div className="flex gap-1">
      <Button size="sm" variant="ghost" disabled={disabled || page <= 1} onClick={() => onChange(page - 1)}>上一页</Button>
      <Button size="sm" variant="ghost" disabled={disabled || page >= pages} onClick={() => onChange(page + 1)}>下一页</Button>
    </div>
  </div>;
}
