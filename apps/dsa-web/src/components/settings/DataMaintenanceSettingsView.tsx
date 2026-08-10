import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  FileBarChart,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react';
import { Badge, Button, EmptyState, InlineAlert } from '../common';
import {
  dataMaintenanceApi,
  type KlineIntegrityStatus,
  type MaintenanceJobStatus,
  type MaintenanceStatus,
} from '../../api/dataMaintenance';
import { cn } from '../../utils/cn';

type JobName = 'stockList' | 'kline' | 'missingKline' | 'financial';
type Notice = { type?: 'error' | 'success'; message: string } | null;

const JOB_LABELS: Record<JobName, string> = {
  stockList: '股票主数据',
  kline: '全市场 K 线',
  missingKline: '缺失 K 线',
  financial: '最新财报',
};

function isRunning(status: MaintenanceStatus | undefined): boolean {
  return status === 'running' || status === 'syncing_kline';
}

function statusLabel(status: MaintenanceStatus | undefined): string {
  if (status === 'running' || status === 'syncing_kline') return '执行中';
  if (status === 'success') return '已完成';
  if (status === 'partial') return '部分完成';
  if (status === 'failed') return '失败';
  return '未执行';
}

function statusVariant(status: MaintenanceStatus | undefined): 'default' | 'success' | 'warning' | 'danger' {
  if (status === 'success') return 'success';
  if (status === 'failed') return 'danger';
  if (status === 'partial') return 'warning';
  if (isRunning(status)) return 'warning';
  return 'default';
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '暂无记录';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function progressOf(job: MaintenanceJobStatus | null, jobName: JobName): number | null {
  if (!job) return null;
  const total = jobName === 'kline' || jobName === 'missingKline'
    ? job.kline_total || job.total
    : job.total;
  const progress = jobName === 'kline' || jobName === 'missingKline'
    ? job.kline_progress ?? job.progress
    : job.progress;
  if (!total) return null;
  return Math.min(100, Math.max(0, Math.round((progress / total) * 100)));
}

function progressText(job: MaintenanceJobStatus | null, jobName: JobName): string {
  if (!job) return '暂无执行记录';
  const total = jobName === 'kline' || jobName === 'missingKline'
    ? job.kline_total || job.total
    : job.total;
  const progress = jobName === 'kline' || jobName === 'missingKline'
    ? job.kline_progress ?? job.progress
    : job.progress;
  if (!total) return job.message || '等待任务开始';
  return `${progress} / ${total}`;
}

function jobMessage(job: MaintenanceJobStatus | null, jobName: JobName): string {
  const message = job?.message?.trim();
  if (isRunning(job?.status)) {
    if (message && message !== '尚未执行') return message;
    return `正在同步${JOB_LABELS[jobName]}...`;
  }
  return message || '尚未执行';
}

function financialCoverage(job: MaintenanceJobStatus | null): string | null {
  if (!job || job.status === 'idle' || job.updated_count === undefined) return null;
  const parts = [`已更新 ${job.updated_count} 只`];
  if (job.no_data_count !== undefined) parts.push(`无可用 ${job.no_data_count} 只`);
  if (job.failed_count !== undefined) parts.push(`失败 ${job.failed_count} 只`);
  if (job.incomplete_count) parts.push(`字段不完整 ${job.incomplete_count} 只`);
  return parts.join(' · ');
}

function JobRow({
  jobName,
  job,
  actionLabel,
  description,
  disabled,
  isStarting,
  onAction,
}: {
  jobName: JobName;
  job: MaintenanceJobStatus | null;
  actionLabel: string;
  description: string;
  disabled: boolean;
  isStarting: boolean;
  onAction: () => void;
}) {
  const progress = progressOf(job, jobName);
  const running = isRunning(job?.status);
  const completed = job?.status === 'success';
  const coverage = jobName === 'financial' ? financialCoverage(job) : null;

  return (
    <article className="py-2.5 first:pt-0 last:pb-0">
      <div className="flex min-w-0 items-center gap-2.5">
        <span className={cn(
          'flex size-7 shrink-0 items-center justify-center rounded-lg',
          running ? 'bg-warning/10 text-warning' : completed ? 'bg-success/10 text-success' : 'bg-primary/10 text-primary',
        )}>
          {running ? <Activity className="size-3.5 animate-pulse" /> : completed ? <CheckCircle2 className="size-3.5" /> : <Database className="size-3.5" />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-1.5">
            <h3 className="truncate text-xs font-semibold text-foreground">{JOB_LABELS[jobName]}</h3>
            <Badge variant={statusVariant(job?.status)} className="shrink-0 px-1.5 py-0 text-[10px]">{statusLabel(job?.status)}</Badge>
          </div>
          <p className="mt-0.5 truncate text-[10px] leading-4 text-muted-foreground">{description}</p>
        </div>
        <div className="hidden w-[min(25%,11rem)] shrink-0 sm:block">
          <div className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
            <span className="truncate">{jobMessage(job, jobName)}</span>
            <span className="shrink-0 font-mono">{progressText(job, jobName)}</span>
          </div>
          {coverage ? <p className="mt-0.5 truncate text-[9px] text-muted-foreground">{coverage}</p> : null}
          {progress !== null ? (
            <div className="mt-1 h-1 overflow-hidden rounded-full bg-border/70">
              <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${progress}%` }} />
            </div>
          ) : null}
        </div>
        <Button
          variant="secondary"
          size="sm"
          className="h-7 shrink-0 rounded-md px-2 text-[10px] leading-4"
          disabled={disabled || running}
          onClick={onAction}
          isLoading={isStarting}
          loadingText="启动中..."
        >
          <RefreshCw className="size-3" />
          {actionLabel}
        </Button>
      </div>

      <div className="mt-1.5 flex items-center gap-2 pl-9 sm:hidden">
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
            <span className="truncate">{jobMessage(job, jobName)}</span>
            <span className="shrink-0 font-mono">{progressText(job, jobName)}</span>
          </div>
          {coverage ? <p className="mt-0.5 truncate text-[9px] text-muted-foreground">{coverage}</p> : null}
          {progress !== null ? (
            <div className="mt-1 h-1 overflow-hidden rounded-full bg-border/70">
              <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${progress}%` }} />
            </div>
          ) : null}
        </div>
      </div>
      {job?.finished_at || job?.error ? (
        <p className="mt-1 pl-9 text-[9px] leading-4 text-muted-foreground">
          {job.finished_at ? `最近完成：${formatDate(job.finished_at)}` : '暂无完成记录'}
          {job.error ? ` · ${job.error}` : ''}
        </p>
      ) : null}
    </article>
  );
}

export function DataMaintenanceSettingsView() {
  const [jobs, setJobs] = useState<Record<JobName, MaintenanceJobStatus | null>>({
    stockList: null,
    kline: null,
    missingKline: null,
    financial: null,
  });
  const [integrity, setIntegrity] = useState<KlineIntegrityStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [starting, setStarting] = useState<JobName | null>(null);
  const [notice, setNotice] = useState<Notice>(null);

  const load = useCallback(async (initial = false, manual = false) => {
    if (initial) setLoading(true);
    else if (manual) setRefreshing(true);
    try {
      const [stockList, kline, missingKline, financial, klineIntegrity] = await Promise.all([
        dataMaintenanceApi.getStockListStatus(),
        dataMaintenanceApi.getKlineStatus(),
        dataMaintenanceApi.getMissingKlineStatus(),
        dataMaintenanceApi.getFinancialStatus(),
        dataMaintenanceApi.getKlineIntegrity(),
      ]);
      setJobs({ stockList, kline, missingKline, financial });
      setIntegrity(klineIntegrity);
    } catch (error) {
      if (initial) setNotice({ type: 'error', message: error instanceof Error ? error.message : '数据维护状态加载失败' });
    } finally {
      if (initial) setLoading(false);
      else if (manual) setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load(true);
  }, [load]);

  const hasRunningJob = useMemo(
    () => Object.values(jobs).some((job) => isRunning(job?.status)),
    [jobs],
  );

  useEffect(() => {
    if (!hasRunningJob) return undefined;
    const timer = window.setInterval(() => {
      void load();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [hasRunningJob, load]);

  const startJob = useCallback(async (jobName: JobName) => {
    if (jobName === 'missingKline' && !integrity?.missing_codes.length) {
      return;
    }
    setStarting(jobName);
    setNotice(null);
    try {
      if (jobName === 'stockList') await dataMaintenanceApi.syncStockList();
      if (jobName === 'kline') await dataMaintenanceApi.syncKline();
      if (jobName === 'missingKline') await dataMaintenanceApi.syncMissingKline(integrity?.missing_codes || []);
      if (jobName === 'financial') await dataMaintenanceApi.syncFinancial();
      await load();
    } catch (error) {
      setNotice({ message: error instanceof Error ? error.message : `${JOB_LABELS[jobName]}任务启动失败` });
    } finally {
      setStarting(null);
    }
  }, [integrity, load]);

  if (loading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <RefreshCw className="size-7 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <section className="space-y-3.5">
      <div className="flex justify-end">
        <Button variant="secondary" size="sm" onClick={() => void load(false, true)} disabled={refreshing} isLoading={refreshing} loadingText="刷新中...">
          <RefreshCw className="size-3.5" />刷新状态
        </Button>
      </div>

      {notice ? (
        <InlineAlert
          variant="danger"
          title="操作失败"
          message={notice.message}
        />
      ) : null}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="rounded-xl border border-border/70 bg-white px-3 py-2.5 shadow-sm">
          <div className="flex items-baseline justify-between gap-2">
            <p className="truncate text-[10px] text-muted-foreground">活跃股票</p>
            <p className="text-lg font-semibold leading-6 text-foreground">{integrity?.total_stocks ?? '—'}</p>
          </div>
          <p className="mt-0.5 truncate text-[10px] text-muted-foreground">主数据：{statusLabel(jobs.stockList?.status)}</p>
        </div>
        <div className="rounded-xl border border-border/70 bg-white px-3 py-2.5 shadow-sm">
          <div className="flex items-baseline justify-between gap-2">
            <p className="truncate text-[10px] text-muted-foreground">已有 K 线</p>
            <p className="text-lg font-semibold leading-6 text-foreground">{integrity?.stocks_with_kline ?? '—'}</p>
          </div>
          <p className="mt-0.5 truncate text-[10px] text-muted-foreground">最近交易日：{integrity?.latest_trading_day || '—'}</p>
        </div>
        <div className={cn(
          'rounded-xl border bg-white px-3 py-2.5 shadow-sm',
          integrity?.missing ? 'border-warning/30' : 'border-border/70',
        )}>
          <div className="flex items-baseline justify-between gap-2">
            <p className="truncate text-[10px] text-muted-foreground">缺失 K 线</p>
            <p className={cn('text-lg font-semibold leading-6', integrity?.missing ? 'text-warning' : 'text-success')}>
              {integrity?.missing ?? '—'}
            </p>
          </div>
          <p className="mt-0.5 truncate text-[10px] text-muted-foreground">可启动缺失数据补齐</p>
        </div>
        <div className="rounded-xl border border-border/70 bg-white px-3 py-2.5 shadow-sm">
          <div className="flex items-baseline justify-between gap-2">
            <p className="truncate text-[10px] text-muted-foreground">最新财报覆盖</p>
            <p className="text-lg font-semibold leading-6 text-foreground">
              {jobs.financial?.status !== 'idle' && jobs.financial?.updated_count !== undefined
                ? `${jobs.financial.updated_count} / ${jobs.financial.total || '—'}`
                : statusLabel(jobs.financial?.status)}
            </p>
          </div>
          <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
            {jobs.financial?.message || '尚未执行'}
          </p>
        </div>
      </div>

      <section className="terminal-card rounded-xl px-3 py-2.5 sm:px-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <ShieldCheck className="size-3.5 text-primary" />
            <h3 className="text-xs font-semibold text-foreground">数据完整性</h3>
            <p className="hidden text-[10px] text-muted-foreground sm:block">按当前活跃股票主数据计算 K 线覆盖。</p>
          </div>
          {integrity?.missing ? (
            <details className="min-w-0">
              <summary className="cursor-pointer text-[10px] font-medium text-warning">
                查看缺失代码（{integrity.missing} 只）
              </summary>
              <div className="mt-2 max-h-28 overflow-y-auto rounded-lg border border-warning/20 bg-warning/5 p-2 font-mono text-[10px] leading-5 text-secondary-text">
                {integrity.missing_codes.join(' · ')}
              </div>
            </details>
          ) : (
            <div className="flex items-center gap-1.5 text-[10px] text-success">
              <CheckCircle2 className="size-3.5" />数据完整
            </div>
          )}
        </div>
        {integrity?.missing ? (
          <div className="mt-1 text-[10px] text-warning sm:hidden">当前有 {integrity.missing} 只股票没有 K 线记录</div>
        ) : null}
        {!integrity ? <EmptyState title="暂时无法读取数据完整性" description="请刷新状态或检查后端服务。" /> : null}
      </section>

      <section className="terminal-card rounded-xl px-3 py-2.5 sm:px-4">
        <div className="flex items-center justify-between gap-2 border-b border-border/60 pb-2">
          <div className="flex min-w-0 items-center gap-2">
            <FileBarChart className="size-3.5 shrink-0 text-primary" />
            <h3 className="text-xs font-semibold text-foreground">同步任务</h3>
            <p className="hidden truncate text-[10px] text-muted-foreground sm:block">服务端后台执行，关闭页面不会中断。</p>
          </div>
          <span className="shrink-0 text-[10px] text-muted-foreground">共 4 项</span>
        </div>
        <div className="divide-y divide-border/60 pt-2.5">
          <JobRow
            jobName="stockList"
            job={jobs.stockList}
            actionLabel="同步主数据"
            description="更新股票代码、名称、市场和行业等基础资料。"
            disabled={starting === 'stockList'}
            isStarting={starting === 'stockList'}
            onAction={() => void startJob('stockList')}
          />
          <JobRow
            jobName="kline"
            job={jobs.kline}
            actionLabel="同步全市场 K 线"
            description="为活跃股票补充本地日线数据，已有最新数据的股票会跳过。"
            disabled={starting === 'kline'}
            isStarting={starting === 'kline'}
            onAction={() => void startJob('kline')}
          />
          <JobRow
            jobName="missingKline"
            job={jobs.missingKline}
            actionLabel="补齐缺失 K 线"
            description="仅处理完整性检查发现没有 K 线记录的股票。"
            disabled={starting === 'missingKline' || !integrity?.missing}
            isStarting={starting === 'missingKline'}
            onAction={() => void startJob('missingKline')}
          />
          <JobRow
            jobName="financial"
            job={jobs.financial}
            actionLabel="同步最新财报"
            description="以活跃股票为分母寻找最近可用报告，必要时回溯报告期并逐股补源。"
            disabled={starting === 'financial'}
            isStarting={starting === 'financial'}
            onAction={() => void startJob('financial')}
          />
        </div>
        {integrity?.missing ? (
          <div className="mt-2 flex items-start gap-1.5 border-t border-border/60 pt-2 text-[10px] leading-4 text-muted-foreground">
            <AlertTriangle className="mt-0.5 size-3 shrink-0 text-warning" />
            缺失 K 线任务按当前检查结果提交代码范围；主数据同步后请先刷新完整性检查。
          </div>
        ) : null}
      </section>
    </section>
  );
}

export default DataMaintenanceSettingsView;
