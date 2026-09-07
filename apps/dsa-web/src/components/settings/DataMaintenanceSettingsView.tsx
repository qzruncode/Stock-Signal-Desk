import { lazy, Suspense, useState } from 'react';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, AlertTriangle, CheckCircle2, Database, FileBarChart, History, RefreshCw, Settings2, ShieldCheck } from 'lucide-react';
import { Badge, Button, ConfirmDialog, EmptyState, InlineAlert } from '../common';
import { dataMaintenanceApi as api, preserveNewerOverview, type DatasetId, type SyncJob } from '../../api/dataMaintenance';
import { useDataServiceStream } from '../../hooks/useDataServiceStream';
import { cn } from '../../utils/cn';
import { active, formatDate, jobMessage, liveQuery, messageOf, statusLabel } from './data-maintenance/presentation';
import { JobControls, type JobInteractions, JobStatus } from './data-maintenance/shared';
import { useMaintenanceJobs } from './data-maintenance/useMaintenanceJobs';

const AutomaticSyncDialog = lazy(() => import('./data-maintenance/AutomaticSyncDialog').then(module => ({ default: module.AutomaticSyncDialog })));
const CoverageDialog = lazy(() => import('./data-maintenance/CoverageDialog').then(module => ({ default: module.CoverageDialog })));
const JobHistoryDialog = lazy(() => import('./data-maintenance/JobHistoryDialog').then(module => ({ default: module.JobHistoryDialog })));
type Panel = { kind: 'policy' | 'coverage' } | { kind: 'history'; job?: SyncJob };
type JobName = 'stockList' | 'kline' | 'missingKline' | 'financial';
const JOBS: Record<JobName, { label: string; dataset: DatasetId; mode: 'stale' | 'missing'; action: string; description: string }> = {
  stockList: { label: '股票主数据', dataset: 'securities', mode: 'stale', action: '同步主数据', description: '更新股票代码、名称、市场和行业等基础资料。' },
  kline: { label: '全市场 K 线', dataset: 'kline', mode: 'stale', action: '同步全市场 K 线', description: '为活跃股票补充日线数据，已有最新数据的股票会跳过。' },
  missingKline: { label: '缺失 K 线', dataset: 'kline', mode: 'missing', action: '补齐缺失 K 线', description: '仅处理完整性检查发现没有 K 线记录的股票。' },
  financial: { label: '最新财报', dataset: 'financials', mode: 'stale', action: '同步最新财报', description: '更新活跃股票的最新可用财报。' },
};

function JobRow({ name, job, disabled, starting, onAction, controls }: {
  name: JobName; job?: SyncJob | null; disabled: boolean; starting: boolean; onAction: () => void; controls: JobInteractions;
}) {
  const config = JOBS[name];
  const running = active(job);
  const progress = job?.total ? Math.min(100, Math.max(0, Math.round(job.progress / job.total * 100))) : null;
  const message = jobMessage(job) || '暂无近期执行记录';
  const progressText = job?.total ? job.progress + ' / ' + job.total : '—';
  const coverage = name === 'financial' && job ? '已更新 ' + job.succeeded + ' 只 · 失败 ' + job.failed + ' 只' : null;
  const progressContent = <>
    <div className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
      <span className="truncate" title={message}>{message}</span>
      <span className="shrink-0 font-mono">{progressText}</span>
    </div>
    {coverage && <p className="mt-0.5 truncate text-[9px] text-muted-foreground">{coverage}</p>}
    {progress !== null && <div className="mt-1 h-1 overflow-hidden rounded-full bg-border/70" role="progressbar" aria-label={config.label + '进度'} aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
      <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: progress + '%' }} />
    </div>}
  </>;
  return <article className="py-2.5 first:pt-0 last:pb-0" aria-label={config.label}>
    <div className="flex min-w-0 items-center gap-2.5">
      <span className={cn('flex size-7 shrink-0 items-center justify-center rounded-lg', running ? 'bg-warning/10 text-warning' : job?.status === 'success' ? 'bg-success/10 text-success' : 'bg-primary/10 text-primary')}>
        {running ? <Activity className="size-3.5 animate-pulse" /> : job?.status === 'success' ? <CheckCircle2 className="size-3.5" /> : <Database className="size-3.5" />}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <h3 className="truncate text-xs font-semibold text-foreground">{config.label}</h3>
          {job ? <JobStatus job={job} /> : <Badge className="shrink-0 px-1.5 py-0 text-[10px]">暂无近期记录</Badge>}
        </div>
        <p className="mt-0.5 truncate text-[10px] leading-4 text-muted-foreground" title={config.description}>{config.description}</p>
      </div>
      <div className="hidden w-[min(25%,11rem)] shrink-0 sm:block">{progressContent}</div>
      <Button variant="secondary" size="sm" className="h-7 shrink-0 rounded-md px-2 text-[10px] leading-4" disabled={disabled || running}
        onClick={onAction} isLoading={starting} loadingText="启动中...">
        <RefreshCw className="size-3" />{config.action}
      </Button>
    </div>
    <div className="mt-1.5 flex items-center gap-2 pl-9 sm:hidden"><div className="min-w-0 flex-1">{progressContent}</div></div>
    {job && <div className="mt-1 flex flex-wrap items-center justify-between gap-x-2 pl-9">
      <p className="min-w-0 flex-1 break-words text-[9px] leading-4 text-muted-foreground">{job.finished_at ? '最近完成：' + formatDate(job.finished_at) : ''}{job.error ? ' · ' + job.error : ''}</p>
      <JobControls {...controls} job={job} />
    </div>}
  </article>;
}

function MissingKlineDetails({ count }: { count: number }) {
  const [open, setOpen] = useState(false);
  return <details className="min-w-0" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary className="cursor-pointer text-[10px] font-medium text-warning">查看缺失代码（{count} 只）</summary>
    {open && <MissingCodes />}
  </details>;
}

function MissingCodes() {
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ['data-service', 'coverage', 'kline', page, 'missing', ''],
    queryFn: () => api.coverage('kline', { page, status: 'missing', search: '' }),
    ...liveQuery, placeholderData: keepPreviousData,
  });
  const pages = Math.max(1, Math.ceil((query.data?.total || 0) / 20));
  return <div className="mt-2 rounded-lg border border-warning/20 bg-warning/5 p-2 text-[10px]">
    {query.isError ? <InlineAlert variant="danger" message="缺失代码读取失败" action={<Button size="sm" onClick={() => query.refetch()}>重试</Button>} /> :
      query.isPending ? <p role="status">正在读取缺失代码…</p> :
        <p className="max-h-28 overflow-y-auto break-words font-mono leading-5 text-secondary-text">{query.data.items.map(item => item.symbol).join(' · ') || '当前页暂无缺失代码'}</p>}
    {query.data && (pages > 1 || page > 1) && <div className="mt-2 flex items-center justify-end gap-2">
      <span>第 {page} 页 · 共 {query.data.total} 只</span>
      <Button size="sm" variant="ghost" disabled={page <= 1 || query.isPlaceholderData} onClick={() => setPage(value => value - 1)}>上一页</Button>
      <Button size="sm" variant="ghost" disabled={page >= pages || query.isPlaceholderData} onClick={() => setPage(value => value + 1)}>下一页</Button>
    </div>}
  </div>;
}

export function DataMaintenanceSettingsView() {
  const cache = useQueryClient();
  const overview = useQuery({ queryKey: ['data-service', 'overview'], queryFn: api.overview, structuralSharing: preserveNewerOverview, ...liveQuery, retry: 1 });
  const klineJobs = useQuery({ queryKey: ['data-service', 'jobs', 'kline', 1], queryFn: () => api.jobs('kline', 1), ...liveQuery, retry: 1 });
  const stream = useDataServiceStream(overview.data?.event_cursor, !overview.isPending);
  const refresh = () => { void cache.invalidateQueries({ queryKey: ['data-service'] }); };
  const mutation = useMaintenanceJobs();
  const [panel, setPanel] = useState<Panel | null>(null);
  const [cancellation, setCancellation] = useState<SyncJob | null>(null);
  const data = overview.data;
  const securities = data?.items.find(item => item.id === 'securities');
  const kline = data?.items.find(item => item.id === 'kline');
  const financial = data?.items.find(item => item.id === 'financials');
  // A submitted or pushed job is authoritative even before the history read completes.
  const latestKline = kline?.latest_job;
  const jobs: Record<JobName, SyncJob | null | undefined> = {
    stockList: securities?.latest_job,
    kline: latestKline && latestKline.mode !== 'missing' ? latestKline : klineJobs.data?.items.find(job => job.mode !== 'missing'),
    missingKline: latestKline?.mode === 'missing' ? latestKline : klineJobs.data?.items.find(job => job.mode === 'missing'),
    financial: financial?.latest_job,
  };
  const connected = !!data && !overview.isError && stream.status === 'live';
  const components = ['scheduler', 'worker', 'sync-worker', 'events'];
  const unhealthy = !!data && components.some(name => {
    const heartbeat = data.service.components[name];
    return !heartbeat?.healthy || stream.now - Date.parse(heartbeat.last_seen_at) >= 120000;
  });
  const klineBusy = active(latestKline) || active(jobs.kline) || active(jobs.missingKline);
  const controls: JobInteractions = {
    busyDatasets: data?.items.filter(item => active(item.latest_job)).map(item => item.id),
    canStart: connected && !unhealthy, canCancel: connected, pending: mutation.isPending,
    onDetails: job => setPanel({ kind: 'history', job }), onCancel: setCancellation,
    onRetry: job => mutation.mutate({ kind: 'retry', job }, { onSuccess: next => setPanel({ kind: 'history', job: next }) }),
  };

  if (overview.isPending) return <div className="flex min-h-[40vh] items-center justify-center" role="status" aria-label="正在读取维护状态"><RefreshCw className="size-7 animate-spin text-primary" /></div>;

  return <section className="space-y-3.5" aria-label="数据维护中心">
    <div className="flex flex-wrap justify-end gap-2">
      <Button variant="secondary" size="sm" disabled={!data?.items.length} onClick={() => setPanel({ kind: 'policy' })}><Settings2 className="size-3.5" />自动同步</Button>
      <Button variant="secondary" size="sm" disabled={!data?.items.length} onClick={() => setPanel({ kind: 'history' })}><History className="size-3.5" />同步记录</Button>
      <Button variant="secondary" size="sm" onClick={refresh} disabled={overview.isFetching} isLoading={overview.isFetching} loadingText="刷新中...">
        <RefreshCw className="size-3.5" />刷新状态
      </Button>
    </div>
    {overview.isError ? <InlineAlert variant="danger" title="状态读取失败" message="请刷新重试；已显示的数值为上次快照。" /> :
      ['reconnecting', 'failed'].includes(stream.status) ? <InlineAlert variant="warning" message="实时连接中断，显示上次快照，恢复后自动更新。"
        action={<Button size="sm" onClick={stream.reconnect}>重新连接</Button>} /> :
        unhealthy ? <InlineAlert variant="warning" message="后台维护暂不可用，请检查数据服务。" /> : null}
    {klineJobs.isError && <InlineAlert variant="warning" message="近期 K 线任务记录读取失败，请刷新重试。" />}
    {mutation.isError && !panel && <InlineAlert variant="danger" title="操作失败" message={messageOf(mutation.error)} />}

    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <div className="rounded-xl border border-border/70 bg-card px-3 py-2.5 shadow-sm" role="group" aria-label="活跃股票统计">
        <div className="flex items-baseline justify-between gap-2"><p className="truncate text-[10px] text-muted-foreground">活跃股票</p><p className="text-lg font-semibold leading-6 text-foreground">{kline?.total ?? '—'}</p></div>
        <p className="mt-0.5 truncate text-[10px] text-muted-foreground">主数据：{statusLabel(jobs.stockList)}</p>
      </div>
      <div className="rounded-xl border border-border/70 bg-card px-3 py-2.5 shadow-sm" role="group" aria-label="已有 K 线统计">
        <div className="flex items-baseline justify-between gap-2"><p className="truncate text-[10px] text-muted-foreground">已有 K 线</p><p className="text-lg font-semibold leading-6 text-foreground">{kline ? kline.total - kline.missing : '—'}</p></div>
        <p className="mt-0.5 truncate text-[10px] text-muted-foreground">最近交易日：{kline?.latest_data_time || '—'}</p>
      </div>
      <div className={cn('rounded-xl border bg-card px-3 py-2.5 shadow-sm', kline?.missing ? 'border-warning/30' : 'border-border/70')} role="group" aria-label="缺失 K 线统计">
        <div className="flex items-baseline justify-between gap-2"><p className="truncate text-[10px] text-muted-foreground">缺失 K 线</p><p className={cn('text-lg font-semibold leading-6', kline?.missing ? 'text-warning' : 'text-success')}>{kline?.missing ?? '—'}</p></div>
        <p className="mt-0.5 truncate text-[10px] text-muted-foreground">可启动缺失数据补齐</p>
      </div>
      <div className="rounded-xl border border-border/70 bg-card px-3 py-2.5 shadow-sm" role="group" aria-label="最新财报覆盖统计">
        <div className="flex items-baseline justify-between gap-2"><p className="truncate text-[10px] text-muted-foreground">最新财报覆盖</p><p className="text-lg font-semibold leading-6 text-foreground">{financial ? financial.fresh + ' / ' + financial.total : '—'}</p></div>
        <p className="mt-0.5 truncate text-[10px] text-muted-foreground">报告期：{financial?.latest_data_time || '—'}</p>
      </div>
    </div>

    <section className="terminal-card rounded-xl px-3 py-2.5 sm:px-4" aria-label="数据完整性">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2"><ShieldCheck className="size-3.5 text-primary" /><h3 className="text-xs font-semibold text-foreground">数据完整性</h3>
          <p className="hidden text-[10px] text-muted-foreground sm:block">按当前活跃股票主数据计算 K 线覆盖。</p></div>
        <div className="flex flex-wrap items-center gap-3">
          {!!kline?.missing && <MissingKlineDetails count={kline.missing} />}
          <Button size="sm" variant="ghost" className="h-6 px-0 text-[10px]" disabled={!data?.items.length} onClick={() => setPanel({ kind: 'coverage' })}>查看数据明细</Button>
        </div>
        {kline && kline.total > 0 && !kline.missing && <div className="flex items-center gap-1.5 text-[10px] text-success"><CheckCircle2 className="size-3.5" />K 线记录齐全</div>}
      </div>
      {!kline ? <EmptyState title="暂时无法读取数据完整性" description="请刷新状态或检查数据服务。" /> :
        kline.total === 0 ? <p className="mt-1 text-[10px] text-muted-foreground">暂无活跃股票，请先同步主数据。</p> : null}
    </section>

    <section className="terminal-card rounded-xl px-3 py-2.5 sm:px-4" aria-label="同步任务">
      <div className="flex items-center justify-between gap-2 border-b border-border/60 pb-2">
        <div className="flex min-w-0 items-center gap-2"><FileBarChart className="size-3.5 shrink-0 text-primary" /><h3 className="text-xs font-semibold text-foreground">同步任务</h3>
          <p className="hidden truncate text-[10px] text-muted-foreground sm:block">服务端后台执行，关闭页面不会中断。</p></div>
        <span className="shrink-0 text-[10px] text-muted-foreground">共 4 项</span>
      </div>
      <div className="divide-y divide-border/60 pt-2.5">
        {(Object.keys(JOBS) as JobName[]).map(name => <JobRow key={name} name={name} job={jobs[name]} controls={controls}
          starting={mutation.isPending && mutation.variables?.kind === 'sync' && mutation.variables.dataset === JOBS[name].dataset && mutation.variables.mode === JOBS[name].mode}
          disabled={!connected || unhealthy || mutation.isPending || (JOBS[name].dataset === 'kline' && (klineBusy || klineJobs.isError)) || (name === 'missingKline' && !kline?.missing)}
          onAction={() => mutation.mutate({ kind: 'sync', dataset: JOBS[name].dataset, mode: JOBS[name].mode })} />)}
      </div>
      {!!kline?.missing && <div className="mt-2 flex items-start gap-1.5 border-t border-border/60 pt-2 text-[10px] leading-4 text-muted-foreground">
        <AlertTriangle className="mt-0.5 size-3 shrink-0 text-warning" />缺失 K 线任务按服务端最新检查结果执行。
      </div>}
    </section>
    <Suspense fallback={<p role="status" className="text-xs text-muted-foreground">正在打开维护面板…</p>}>
      {data && panel?.kind === 'policy' && <AutomaticSyncDialog datasets={data.items} connected={connected} onClose={() => setPanel(null)} />}
      {data && panel?.kind === 'coverage' && <CoverageDialog datasets={data.items} connected={connected} canStart={controls.canStart} pending={mutation.isPending}
        error={mutation.isError ? messageOf(mutation.error) : undefined}
        onClose={() => setPanel(null)} onSync={dataset => mutation.mutate({ kind: 'sync', dataset, mode: 'stale' }, { onSuccess: job => setPanel({ kind: 'history', job }) })} />}
      {data && panel?.kind === 'history' && <JobHistoryDialog key={panel.job?.id || 'history'} datasets={data.items} job={panel.job} controls={controls} connected={connected}
        error={mutation.isError ? messageOf(mutation.error) : undefined} onClose={() => setPanel(null)} />}
    </Suspense>
    <ConfirmDialog isOpen={!!cancellation} title="取消同步任务？" message={connected ? '停止处理后续项目，已经保存的数据会保留。' : '连接已中断，请恢复连接后再确认。'}
      confirmText="确认取消" cancelText="继续执行" onCancel={() => setCancellation(null)} onConfirm={() => {
        if (!cancellation || !connected || mutation.isPending) return;
        mutation.mutate({ kind: 'cancel', job: cancellation }); setCancellation(null);
      }} />
  </section>;
}

export default DataMaintenanceSettingsView;
