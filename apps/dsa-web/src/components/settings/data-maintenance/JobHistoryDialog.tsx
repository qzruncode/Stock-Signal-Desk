import { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { ArrowLeft, RefreshCw } from 'lucide-react';
import { Button, EmptyState, FormCheckbox, InlineAlert, Modal, Select } from '../../common';
import { dataMaintenanceApi as api, type DatasetId, type DatasetSummary, type SyncJob } from '../../../api/dataMaintenance';
import { datasetNames, formatDate, jobMessage, liveQuery, messageOf } from './presentation';
import { JobControls, type JobInteractions, JobStatus, PageNavigation } from './shared';

function TaskDetail({ job, controls, onBack }: { job: SyncJob; controls: JobInteractions; onBack: () => void }) {
  const [filter, setFilter] = useState({ page: 1, failures: false });
  const query = useQuery({ queryKey: ['data-service', 'job', job.id, filter.page, filter.failures], queryFn: () => api.job(job.id, filter.page, filter.failures), ...liveQuery, placeholderData: keepPreviousData });
  const current = query.data || job;
  const progress = current.total ? Math.min(100, Math.round(current.progress / current.total * 100)) : 0;
  const itemLabels: Record<string, string> = { queued: '待处理', running: '执行中', success: '成功', failed: '失败', cancelled: '已取消' };
  return <div className="space-y-3">
    <Button variant="ghost" size="sm" className="px-0" onClick={onBack}><ArrowLeft className="size-3.5" />返回同步记录</Button>
    <div className="flex flex-wrap items-center justify-between gap-2"><JobStatus job={current} /><span className="text-xs text-muted-foreground">成功 {current.succeeded} · 失败 {current.failed} · 已处理 {current.progress} / {current.total}</span></div>
    <div className="h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-label="任务处理进度" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
      <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: progress + '%' }} />
    </div>
    <p className="break-words text-xs">{jobMessage(current) || '等待执行'}</p>
    {current.error && <InlineAlert variant="danger" message={current.error} />}
    <div className="flex flex-wrap items-center justify-between gap-2">
      <FormCheckbox label="只看失败项" checked={filter.failures} onChange={failures => setFilter({ failures, page: 1 })} />
      <div className="flex items-center gap-1"><JobControls {...controls} onDetails={undefined} job={current} />
        <Button size="sm" variant="ghost" aria-label="刷新任务详情" disabled={query.isFetching} onClick={() => query.refetch()}><RefreshCw className="size-3.5" /></Button></div>
    </div>
    {query.isError ? <InlineAlert variant="danger" message={messageOf(query.error)} action={<Button size="sm" onClick={() => query.refetch()}>重试</Button>} /> :
      query.isPending ? <p role="status" className="py-8 text-center text-xs text-muted-foreground">正在读取任务详情…</p> :
        <div className="max-h-[42vh] overflow-auto rounded-lg border border-border/60" aria-busy={query.isFetching}>
          <table className="w-full min-w-[420px] text-left text-xs"><thead className="sticky top-0 bg-elevated"><tr>{['证券 / 订阅', '状态', '尝试次数', '失败原因'].map(label => <th className="p-2.5 font-medium" key={label}>{label}</th>)}</tr></thead>
            <tbody>{query.data.items.map(item => <tr key={item.symbol} className="border-t border-border/50 align-top"><td className="max-w-48 break-all p-2.5 font-mono">{item.symbol}</td><td className="whitespace-nowrap p-2.5">{itemLabels[item.status] || item.status}</td><td className="p-2.5">{item.attempts}</td><td className="max-w-72 break-words p-2.5 text-danger">{item.error || '—'}</td></tr>)}
              {!query.data.items.length && <tr><td colSpan={4} className="p-8 text-center text-muted-foreground">{filter.failures ? '没有失败项' : '尚无处理明细'}</td></tr>}</tbody>
          </table>
        </div>}
    {query.data && <PageNavigation page={filter.page} total={query.data.items_total} disabled={query.isPlaceholderData} onChange={page => setFilter(value => ({ ...value, page }))} />}
    <div className="space-y-1 text-[10px] text-muted-foreground"><p>开始：{formatDate(current.started_at)} · 完成：{formatDate(current.finished_at)}</p><p className="break-all font-mono">任务 {current.id}</p></div>
  </div>;
}

function History({ dataset, page, datasets, onDataset, onPage, controls }: {
  dataset: DatasetId | 'all'; page: number; datasets: DatasetSummary[]; onDataset: (dataset: DatasetId | 'all') => void; onPage: (page: number) => void; controls: JobInteractions;
}) {
  const query = useQuery({ queryKey: ['data-service', 'jobs', dataset, page], queryFn: () => api.jobs(dataset === 'all' ? undefined : dataset, page), ...liveQuery,
    placeholderData: (previous, previousQuery) => previousQuery?.queryKey[2] === dataset ? keepPreviousData(previous) : undefined });
  return <div className="space-y-3">
    <div className="flex items-center gap-2"><div className="min-w-0 flex-1"><Select density="compact" ariaLabel="筛选同步数据集" value={dataset} onChange={value => onDataset(value as DatasetId | 'all')}
      options={[{ value: 'all', label: '全部数据集' }, ...datasets.map(item => ({ value: item.id, label: datasetNames[item.id] }))]} /></div>
      <Button variant="ghost" size="sm" aria-label="刷新同步记录" disabled={query.isFetching} onClick={() => query.refetch()}><RefreshCw className="size-3.5" /></Button></div>
    {query.isError ? <InlineAlert variant="danger" message={messageOf(query.error)} action={<Button size="sm" onClick={() => query.refetch()}>重试</Button>} /> :
      query.isPending ? <p role="status" className="py-8 text-center text-xs text-muted-foreground">正在读取同步记录…</p> :
        !query.data.items.length ? <EmptyState title="暂无同步记录" description="更换数据集或返回任务列表发起同步。" /> :
          <div className="max-h-[52vh] divide-y divide-border/60 overflow-y-auto rounded-lg border border-border/60" aria-busy={query.isFetching}>
            {query.data.items.map(job => <div key={job.id} className="space-y-2 p-3" role="group" aria-label={'任务 ' + job.id}>
              <div className="flex flex-wrap items-center gap-2"><span className="text-xs font-semibold">{datasetNames[job.dataset]}</span><JobStatus job={job} />
                <span className="text-[10px] text-muted-foreground">{job.trigger === 'scheduled' ? '自动' : job.trigger === 'retry' ? '重试' : '手动'} · {formatDate(job.created_at)}</span></div>
              <p className="truncate text-xs text-muted-foreground" title={jobMessage(job)}>{jobMessage(job) || '等待执行'}</p>
              <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-[10px] text-muted-foreground">已处理 {job.progress} / {job.total} · 失败 {job.failed}</span><JobControls {...controls} job={job} /></div>
            </div>)}
          </div>}
    {query.data && <PageNavigation page={page} total={query.data.total} disabled={query.isPlaceholderData} onChange={onPage} />}
  </div>;
}

export function JobHistoryDialog({ datasets, job, controls, error, connected, onClose }: {
  datasets: DatasetSummary[]; job?: SyncJob; controls: JobInteractions; error?: string; connected: boolean; onClose: () => void;
}) {
  const [selected, setSelected] = useState(job);
  const [filter, setFilter] = useState<{ dataset: DatasetId | 'all'; page: number }>({ dataset: job?.dataset || 'all', page: 1 });
  return <Modal isOpen onClose={onClose} title={selected ? datasetNames[selected.dataset] + ' · 任务详情' : '同步记录'} width="max-w-3xl">
    <div className="space-y-3">
      {!connected && <InlineAlert variant="warning" message="连接中断，当前任务信息可能不是最新状态。" />}
      {error && <InlineAlert variant="danger" message={error} />}
      {selected ? <TaskDetail key={selected.id} job={selected} controls={controls} onBack={() => setSelected(undefined)} /> :
        <History {...filter} datasets={datasets} onDataset={dataset => setFilter({ dataset, page: 1 })} onPage={page => setFilter(value => ({ ...value, page }))}
          controls={{ ...controls, onDetails: setSelected }} />}
    </div>
  </Modal>;
}
