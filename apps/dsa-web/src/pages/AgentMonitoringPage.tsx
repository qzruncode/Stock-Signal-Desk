import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Database,
  Gauge,
  HardDrive,
  HeartPulse,
  Monitor,
  Network,
  RefreshCw,
  RotateCcw,
  ServerCog,
  ShieldAlert,
  Timer,
  Wrench,
  XCircle,
  Zap,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import {
  agentMonitoringApi,
  type AgentMetricAlert,
  type AgentMetricsResponse,
  type AgentReadinessCheck,
  type AgentReadinessResponse,
} from '../api/agentMonitoring';
import { toApiErrorMessage } from '../api/error';
import { Badge, Card, StatusDot } from '../components/common';
import { Button } from '../components/ui/button';
import { cn } from '../utils/cn';
import { formatDateTime } from '../utils/format';

const REFRESH_INTERVAL_MS = 60_000;

type SignalSeverity = 'critical' | 'warning' | 'info';
type StatusTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral';

interface SystemSignal {
  id: string;
  severity: SignalSeverity;
  title: string;
  description: string;
  detail?: string;
  href?: string;
}

const READINESS_LABELS: Record<string, string> = {
  database: '数据库连接',
  model: '模型配置',
  runtime: 'Agent 运行时',
  tools: '工具目录',
  dependencies: '外部依赖探针',
};

const DEPENDENCY_LABELS: Record<string, string> = {
  modelProvider: '模型服务',
  toolCatalog: '工具目录',
};

const ALERT_COPY: Record<string, { title: string; description: string }> = {
  agentSuccessRateBelowSlo: {
    title: '系统完成率低于 SLO',
    description: '近 24 小时的 Agent 终态完成率低于配置目标。',
  },
  agentLatencyAboveSlo: {
    title: '系统响应耗时超过 SLO',
    description: '近 24 小时的 P95 运行耗时超过配置目标。',
  },
  agentExpiredRunLeases: {
    title: '运行时租约未及时释放',
    description: '发现过期的运行租约，可能导致容量长期被占用。',
  },
  agentDependencyCircuitOpen: {
    title: '共享依赖熔断',
    description: '某个模型或工具依赖已经进入熔断保护状态。',
  },
};

const numberValue = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return null;
};

const countValue = (value: unknown): number => numberValue(value) ?? 0;

const objectValue = (value: unknown): Record<string, unknown> | null => (
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
);

const formatCount = (value: unknown): string => {
  const number = numberValue(value);
  return number == null ? '—' : new Intl.NumberFormat('zh-CN').format(number);
};

const formatPercent = (value: unknown): string => {
  const number = numberValue(value);
  return number == null ? '—' : `${Math.round(number * 100)}%`;
};

const formatDuration = (value: unknown): string => {
  const number = numberValue(value);
  if (number == null) return '—';
  if (number < 1000) return `${Math.round(number)} ms`;
  if (number < 60_000) return `${(number / 1000).toFixed(1)} 秒`;
  return `${Math.floor(number / 60_000)} 分 ${Math.round((number % 60_000) / 1000)} 秒`;
};

const formatDate = (value: unknown): string => (
  typeof value === 'string' && value ? formatDateTime(value) : '—'
);

const severityTone = (severity: SignalSeverity): StatusTone => {
  if (severity === 'critical') return 'danger';
  if (severity === 'warning') return 'warning';
  return 'info';
};

const severityLabel = (severity: SignalSeverity): string => {
  if (severity === 'critical') return '严重';
  if (severity === 'warning') return '警告';
  return '提示';
};

const normalizeAlertCode = (code: string): string => code.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase());

const alertDetail = (alert: AgentMetricAlert): string => {
  if (alert.code === 'agent_success_rate_below_slo') {
    return `实际 ${formatPercent(alert.value)} · 阈值 ${formatPercent(alert.threshold)}`;
  }
  if (alert.code === 'agent_latency_above_slo') {
    return `实际 ${formatDuration(alert.value)} · 阈值 ${formatDuration(alert.threshold)}`;
  }
  return `当前 ${formatCount(alert.value)} · 目标 ${formatCount(alert.threshold)}`;
};

const readinessDetail = (name: string, check: AgentReadinessCheck): string => {
  if (typeof check.error === 'string' && check.error) return check.error;
  if (name === 'database') {
    return `schema ${String(check.schemaVersion ?? '—')} · 期望 ${String(check.expectedSchemaVersion ?? '—')}`;
  }
  if (name === 'model') return typeof check.model === 'string' ? check.model : '模型配置已加载';
  if (name === 'runtime') {
    const engine = typeof check.engine === 'string' ? check.engine : 'Agent 运行时';
    const workers = numberValue(check.workers);
    const graph = check.graphInitialized === false ? '图未初始化' : '图已初始化';
    return workers == null ? `${engine} · ${graph}` : `${engine} · ${workers} 个 worker · ${graph}`;
  }
  if (name === 'tools') {
    return `${formatCount(check.registered)} 个工具 · ${formatCount(check.operationDirectory)} 个目录项`;
  }
  return check.ok === true ? '检查通过' : '暂无详细信息';
};

const buildSystemSignals = (
  metricsResponse: AgentMetricsResponse | null,
  readiness: AgentReadinessResponse | null,
): SystemSignal[] => {
  const signals: SystemSignal[] = [];
  const failedChecks = Object.entries(readiness?.checks ?? {})
    .filter(([, check]) => check.ok === false)
    .map(([name]) => READINESS_LABELS[name] ?? name);

  if (failedChecks.length > 0) {
    signals.push({
      id: 'readiness',
      severity: 'critical',
      title: '控制面存在未通过检查',
      description: `未通过：${failedChecks.join('、')}`,
      detail: '先处理基础设施或依赖状态，再判断单次运行结果。',
    });
  }

  for (const alert of metricsResponse?.alerts ?? []) {
    const normalizedCode = normalizeAlertCode(alert.code);
    const copy = ALERT_COPY[normalizedCode] ?? {
      title: alert.code || '运行时告警',
      description: '监控接口返回了一条系统级运行时信号。',
    };
    const isRunDetailSignal = alert.code === 'agent_success_rate_below_slo' || alert.code === 'agent_latency_above_slo';
    signals.push({
      id: alert.code,
      severity: alert.severity === 'critical' ? 'critical' : 'warning',
      title: copy.title,
      description: copy.description,
      detail: alertDetail(alert),
      href: isRunDetailSignal ? '/setting?tab=runs&status=failed' : undefined,
    });
  }

  const runtime = readiness?.checks.runtime;
  const limits = objectValue(runtime?.limits);
  const activeRuns = numberValue(runtime?.activeRuns);
  const maxActiveRuns = numberValue(limits?.maxActiveRuns);
  if (activeRuns != null && maxActiveRuns != null && maxActiveRuns > 0 && activeRuns / maxActiveRuns >= 0.8) {
    signals.push({
      id: 'capacity',
      severity: activeRuns >= maxActiveRuns ? 'critical' : 'warning',
      title: 'Agent 活跃容量接近上限',
      description: `当前占用 ${formatCount(activeRuns)} / ${formatCount(maxActiveRuns)} 个活跃槽位。`,
      detail: '新增请求可能排队或被容量保护拒绝。',
    });
  }

  const persistence = objectValue(runtime?.eventPersistence);
  const batchFailures = numberValue(persistence?.batchFailures);
  if (batchFailures != null && batchFailures > 0) {
    signals.push({
      id: 'event-persistence',
      severity: 'critical',
      title: '事件持久化出现失败',
      description: '运行事件没有全部写入持久化存储，可能影响恢复与审计。',
      detail: `${formatCount(batchFailures)} 次批量写入失败`,
    });
  }

  return signals.sort((left, right) => {
    const rank = { critical: 0, warning: 1, info: 2 };
    return rank[left.severity] - rank[right.severity];
  });
};

const SummaryCell: React.FC<{
  label: string;
  value: string;
  hint: string;
  icon: React.ComponentType<{ className?: string }>;
  tone?: 'primary' | 'success' | 'warning' | 'danger';
}> = ({ label, value, hint, icon: Icon, tone = 'primary' }) => {
  const toneClass = {
    primary: 'border-primary/20 bg-primary/5 text-primary',
    success: 'border-success/20 bg-success/5 text-success',
    warning: 'border-warning/20 bg-warning/5 text-warning',
    danger: 'border-danger/20 bg-danger/5 text-danger',
  }[tone];

  return (
    <div className="min-w-0 bg-card px-3 py-2">
      <div className="flex min-h-[68px] items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[10px] text-secondary-text">{label}</p>
          <p className="mt-0.5 truncate text-base font-semibold leading-5 text-foreground">{value}</p>
          <p className="mt-1 truncate text-[9px] text-secondary-text" title={hint}>{hint}</p>
        </div>
        <span className={cn('flex size-6 shrink-0 items-center justify-center rounded-md border', toneClass)}>
          <Icon className="size-3.5" />
        </span>
      </div>
    </div>
  );
};

const AgentMonitoringPage: React.FC<{ embedded?: boolean }> = ({ embedded = false }) => {
  const [metricsResponse, setMetricsResponse] = useState<AgentMetricsResponse | null>(null);
  const [readiness, setReadiness] = useState<AgentReadinessResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [probeLoading, setProbeLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [metricsResult, readinessResult] = await Promise.allSettled([
      agentMonitoringApi.getMetrics(),
      agentMonitoringApi.getReadiness(),
    ]);
    const failures: string[] = [];
    if (metricsResult.status === 'fulfilled') setMetricsResponse(metricsResult.value);
    else failures.push(toApiErrorMessage(metricsResult.reason, '系统指标加载失败'));
    if (readinessResult.status === 'fulfilled') setReadiness(readinessResult.value);
    else failures.push(toApiErrorMessage(readinessResult.reason, '系统就绪状态加载失败'));
    setError(failures.length > 0 ? failures.join('；') : null);
    setLoading(false);
  }, []);

  const runDependencyProbe = useCallback(async () => {
    setProbeLoading(true);
    setError(null);
    try {
      setReadiness(await agentMonitoringApi.getReadiness({ deep: true }));
    } catch (requestError) {
      setError(toApiErrorMessage(requestError, '依赖探针执行失败'));
    } finally {
      setProbeLoading(false);
    }
  }, []);

  useEffect(() => {
    document.title = '系统运行监控 - Stock Assistant';
  }, []);

  useEffect(() => {
    const initialLoad = window.setTimeout(() => {
      void load();
    }, 0);
    const timer = window.setInterval(() => {
      void load();
    }, REFRESH_INTERVAL_MS);
    return () => {
      window.clearTimeout(initialLoad);
      window.clearInterval(timer);
    };
  }, [load]);

  const metrics = metricsResponse?.metrics ?? null;
  const signals = useMemo(() => buildSystemSignals(metricsResponse, readiness), [metricsResponse, readiness]);
  const runtime = readiness?.checks.runtime;
  const limits = objectValue(runtime?.limits);
  const activeRuns = numberValue(runtime?.activeRuns);
  const maxActiveRuns = numberValue(limits?.maxActiveRuns);
  const capacityRatio = activeRuns != null && maxActiveRuns ? activeRuns / maxActiveRuns : null;
  const oldestActiveSeconds = numberValue(runtime?.oldestActiveSeconds);
  const persistence = objectValue(runtime?.eventPersistence);
  const executionCache = objectValue(runtime?.executionCache);
  const dependencies = objectValue(readiness?.checks.dependencies);
  const dependencyChecks = objectValue(dependencies?.checks);
  const slo = metrics?.slo24h;
  const workload = metrics?.workload24h;
  const persistenceBatches = numberValue(persistence?.batches);
  const openCircuits = countValue(metrics?.openCircuits);
  const halfOpenCircuits = countValue(metrics?.halfOpenCircuits);
  const expiredCircuits = countValue(metrics?.expiredCircuits);
  const activeCircuits = metrics == null ? null : openCircuits + halfOpenCircuits;
  const lastCheckedAt = [metricsResponse?.checkedAt, readiness?.checkedAt]
    .filter((value): value is string => Boolean(value))
    .sort((left, right) => Date.parse(right) - Date.parse(left))[0];

  const monitorState = useMemo(() => {
    if (!metricsResponse && !readiness && loading) return { label: '正在检查', dot: 'info' as StatusTone };
    if (signals.some((signal) => signal.severity === 'critical')) return { label: '严重异常', dot: 'danger' as StatusTone };
    if (signals.length > 0) return { label: '有系统风险', dot: 'warning' as StatusTone };
    if (!metricsResponse && !readiness) return { label: '暂无数据', dot: 'neutral' as StatusTone };
    return { label: '系统正常', dot: 'success' as StatusTone };
  }, [loading, metricsResponse, readiness, signals]);

  const monitorSurfaceClass = {
    danger: 'bg-danger/[0.04]',
    warning: 'bg-warning/[0.04]',
    success: 'bg-success/[0.04]',
    info: 'bg-primary/[0.04]',
    neutral: 'bg-card',
  }[monitorState.dot];
  const monitorIconClass = {
    danger: 'text-danger',
    warning: 'text-warning',
    success: 'text-success',
    info: 'text-primary',
    neutral: 'text-muted-foreground',
  }[monitorState.dot];

  const coreReadinessRows = ['database', 'model', 'runtime', 'tools'].map((name) => ({
    name,
    check: readiness?.checks[name],
  }));

  return (
    <div className={cn('min-h-full space-y-4', !embedded && 'mx-auto max-w-[1500px] py-4')}>
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border/70 pb-4">
        <div>
          {!embedded ? (
            <Link
              to="/"
              viewTransition
              className="mb-2 inline-flex items-center gap-1 text-xs text-secondary-text transition hover:text-foreground"
            >
              <ArrowLeft className="size-3.5" />
              返回助手
            </Link>
          ) : null}
          <div className="flex items-center gap-2">
            <span className="flex size-8 items-center justify-center rounded-lg border border-primary/25 bg-primary/8 text-primary">
              <Monitor className="size-4" />
            </span>
            <div>
              <h1 className="text-xl font-semibold text-foreground">系统运行监控</h1>
              <p className="mt-0.5 text-xs text-secondary-text">只关注系统级可用性、容量、依赖和运行时异常</p>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="hidden text-[11px] text-secondary-text sm:inline">
            {lastCheckedAt ? `状态更新于 ${formatDate(lastCheckedAt)}` : '等待首次检查'}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void runDependencyProbe()}
            disabled={probeLoading || loading}
          >
            <Network className={cn('size-3.5', probeLoading && 'animate-pulse')} />
            {probeLoading ? '探针执行中' : '执行依赖检查'}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void load()}
            disabled={loading}
            aria-label="刷新系统运行监控"
          >
            <RefreshCw className={cn('size-3.5', loading && 'animate-spin')} />
            刷新
          </Button>
        </div>
      </header>

      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-danger/20 bg-danger/8 px-3 py-2.5 text-xs text-danger" role="alert">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <section className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-border bg-border md:grid-cols-[1.25fr_repeat(3,minmax(0,1fr))]">
        <div className={cn('min-w-0 bg-card px-3 py-2', monitorSurfaceClass)}>
          <div className="flex min-h-[68px] items-center justify-between gap-2">
            <div>
              <p className="text-[10px] text-secondary-text">系统状态</p>
              <div className="mt-0.5 flex items-center gap-1.5">
                <StatusDot tone={monitorState.dot} pulse={monitorState.dot === 'info'} aria-label={monitorState.label} />
                <span className="text-base font-semibold leading-5 text-foreground">{monitorState.label}</span>
              </div>
              <p className="mt-1 truncate text-[9px] text-secondary-text">
                {signals.length > 0 ? `${signals.length} 个系统级异常信号` : '没有发现系统级异常信号'}
              </p>
            </div>
            <HeartPulse className={cn('size-4 shrink-0', monitorIconClass)} />
          </div>
        </div>
        <SummaryCell
          label="控制面就绪"
          value={readiness ? (readiness.ready ? '正常' : '异常') : '—'}
          hint="数据库、模型配置、运行时和工具目录"
          icon={CheckCircle2}
          tone={readiness?.ready === false ? 'danger' : 'success'}
        />
        <SummaryCell
          label="活跃容量"
          value={activeRuns != null && maxActiveRuns != null ? `${formatCount(activeRuns)} / ${formatCount(maxActiveRuns)}` : '—'}
          hint={capacityRatio == null ? '等待运行时容量数据' : `已占用 ${Math.round(capacityRatio * 100)}%`}
          icon={Gauge}
          tone={capacityRatio != null && capacityRatio >= 0.8 ? 'warning' : 'primary'}
        />
        <SummaryCell
          label="依赖熔断"
          value={activeCircuits == null ? '—' : formatCount(activeCircuits)}
          hint={activeCircuits == null
            ? '等待依赖熔断数据'
            : `${formatCount(openCircuits)} 个打开 · ${formatCount(halfOpenCircuits)} 个半开 · ${formatCount(expiredCircuits)} 个已过期`}
          icon={ShieldAlert}
          tone={activeCircuits == null ? 'primary' : activeCircuits > 0 ? 'danger' : expiredCircuits > 0 ? 'warning' : 'success'}
        />
      </section>

      <section className="grid gap-4 lg:grid-cols-[1.15fr_0.85fr]">
        <Card padding="none" className="overflow-hidden rounded-xl">
          <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold text-foreground">系统异常</h2>
              <p className="mt-0.5 text-[11px] text-secondary-text">只列控制面、容量、依赖和持久化层异常</p>
            </div>
            <Badge variant={signals.length > 0 ? 'danger' : 'success'}>
              {signals.length > 0 ? `${signals.length} 个` : '正常'}
            </Badge>
          </div>
          <div className="divide-y divide-border/70">
            {signals.length === 0 ? (
              <div className="flex items-center gap-2 px-4 py-6 text-xs text-secondary-text">
                <CheckCircle2 className="size-4 text-success" />
                当前没有发现需要人工处理的系统级异常。
              </div>
            ) : signals.map((signal) => {
              const tone = severityTone(signal.severity);
              const content = (
                <>
                  <span className={cn(
                    'mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg',
                    tone === 'danger' ? 'bg-danger/10 text-danger' : tone === 'warning' ? 'bg-warning/10 text-warning' : 'bg-primary/10 text-primary',
                  )}>
                    {tone === 'danger' ? <ShieldAlert className="size-4" /> : <CircleAlert className="size-4" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-xs font-medium text-foreground">{signal.title}</span>
                      <Badge variant={tone === 'danger' ? 'danger' : tone === 'warning' ? 'warning' : 'info'}>
                        {severityLabel(signal.severity)}
                      </Badge>
                    </span>
                    <span className="mt-1 block text-[11px] leading-5 text-secondary-text">{signal.description}</span>
                    {signal.detail ? <span className="mt-1 block text-[10px] text-secondary-text">{signal.detail}</span> : null}
                  </span>
                  {signal.href ? <ArrowRight className="mt-1 size-4 shrink-0 text-secondary-text" /> : null}
                </>
              );
              return signal.href ? (
                <Link key={signal.id} to={signal.href} className="flex items-start gap-3 px-4 py-3 transition hover:bg-muted/50">
                  {content}
                </Link>
              ) : (
                <div key={signal.id} className="flex items-start gap-3 px-4 py-3">
                  {content}
                </div>
              );
            })}
          </div>
        </Card>

        <Card padding="none" className="overflow-hidden rounded-xl">
          <div className="border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold text-foreground">基础设施状态</h2>
            <p className="mt-0.5 text-[11px] text-secondary-text">控制面启动前必须满足的检查项</p>
          </div>
          <div className="divide-y divide-border/70">
            {coreReadinessRows.map(({ name, check }) => {
              const ok = check?.ok === true;
              return (
                <div key={name} className="flex items-center gap-3 px-4 py-2.5">
                  <span className={cn(
                    'flex size-7 shrink-0 items-center justify-center rounded-lg',
                    check == null ? 'bg-muted text-muted-foreground' : ok ? 'bg-success/10 text-success' : 'bg-danger/10 text-danger',
                  )}>
                    {check == null ? <Clock3 className="size-4" /> : ok ? <CheckCircle2 className="size-4" /> : <XCircle className="size-4" />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium text-foreground">{READINESS_LABELS[name]}</p>
                    <p className="mt-0.5 truncate text-[10px] text-secondary-text" title={check ? readinessDetail(name, check) : '等待检查'}>
                      {check ? readinessDetail(name, check) : '等待检查'}
                    </p>
                  </div>
                  <Badge variant={check == null ? 'default' : ok ? 'success' : 'danger'}>
                    {check == null ? '未检查' : ok ? '正常' : '异常'}
                  </Badge>
                </div>
              );
            })}
          </div>
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
        <Card padding="none" className="overflow-hidden rounded-xl">
          <div className="border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold text-foreground">运行时容量与配置</h2>
            <p className="mt-0.5 text-[11px] text-secondary-text">判断系统是否会因为资源上限而拒绝或拖慢请求</p>
          </div>
          <div className="grid grid-cols-2 gap-px bg-border/70 sm:grid-cols-3">
            {[
              ['活跃运行', activeRuns != null && maxActiveRuns != null ? `${formatCount(activeRuns)} / ${formatCount(maxActiveRuns)}` : '—'],
              ['最长运行', activeRuns === 0 ? '无活跃运行' : oldestActiveSeconds == null ? '—' : formatDuration(oldestActiveSeconds * 1000)],
              ['Worker 数', formatCount(runtime?.workers)],
              ['请求限流', numberValue(limits?.requestsPerMinute) === 0 ? '未启用' : `${formatCount(limits?.requestsPerMinute)} / 分钟`],
              ['图状态', runtime?.graphInitialized === true ? '已初始化' : runtime?.graphInitialized === false ? '未初始化' : '—'],
              ['检查点', typeof runtime?.checkpointer === 'string' ? runtime.checkpointer : '—'],
            ].map(([label, value]) => (
              <div key={label} className="bg-card px-3 py-3">
                <p className="text-[10px] text-secondary-text">{label}</p>
                <p className="mt-1 truncate text-sm font-semibold text-foreground" title={value}>{value}</p>
              </div>
            ))}
          </div>
          {Array.isArray(runtime?.issues) && runtime.issues.length > 0 ? (
            <div className="border-t border-border bg-danger/5 px-4 py-3 text-[11px] text-danger">
              <p className="font-medium">运行时配置问题</p>
              <ul className="mt-1 space-y-1">
                {runtime.issues.map((issue) => <li key={String(issue)}>· {String(issue)}</li>)}
              </ul>
            </div>
          ) : null}
        </Card>

        <Card padding="none" className="overflow-hidden rounded-xl">
          <div className="border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold text-foreground">可靠性机制</h2>
            <p className="mt-0.5 text-[11px] text-secondary-text">恢复、租约、事件和幂等机制是否健康</p>
          </div>
          <div className="grid grid-cols-2 gap-px bg-border/70 sm:grid-cols-3 lg:grid-cols-2">
            {([
              ['打开熔断', metrics?.openCircuits, ShieldAlert],
              ['半开熔断', metrics?.halfOpenCircuits, ShieldAlert],
              ['已过期熔断', metrics?.expiredCircuits, Timer],
              ['过期租约', metrics?.expiredRunLeases, Timer],
              ['活动资源租约', metrics?.activeResourceLeases, HardDrive],
              ['24h 恢复尝试', metrics?.recoveryAttempts24h, RotateCcw],
              ['幂等复用', metrics?.stepIdempotencyReuses, Zap],
              ['事件写入失败', persistence?.batchFailures, Database],
            ] as Array<[string, unknown, React.ComponentType<{ className?: string }>]>)
              .map(([label, value, Icon]) => {
              const count = countValue(value);
              const isIssue = ['打开熔断', '半开熔断', '过期租约', '事件写入失败'].includes(String(label)) && count > 0;
              const isExpiredCircuit = label === '已过期熔断' && count > 0;
              return (
                <div key={String(label)} className="flex items-center gap-2 bg-card px-3 py-3">
                  <Icon className={cn('size-4 shrink-0', isIssue ? 'text-danger' : isExpiredCircuit ? 'text-warning' : 'text-primary')} />
                  <div className="min-w-0">
                    <p className="truncate text-[10px] text-secondary-text">{label}</p>
                    <p className={cn('mt-0.5 text-sm font-semibold', isIssue ? 'text-danger' : isExpiredCircuit ? 'text-warning' : 'text-foreground')}>{formatCount(value)}</p>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
        <Card padding="none" className="overflow-hidden rounded-xl">
          <div className="border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold text-foreground">系统级 SLO 与吞吐</h2>
            <p className="mt-0.5 text-[11px] text-secondary-text">只展示聚合指标，不展开单次运行和工具明细</p>
          </div>
          <div className="grid grid-cols-2 gap-px bg-border/70 sm:grid-cols-4">
            {([
              ['完成率', formatPercent(slo?.successRate), CheckCircle2],
              ['P95 耗时', formatDuration(slo?.durationMsP95), Timer],
              ['模型调用', formatCount(workload?.providerCalls), ServerCog],
              ['事件量', formatCount(workload?.events), Activity],
            ] as Array<[string, string, React.ComponentType<{ className?: string }>]>)
              .map(([label, value, Icon]) => (
              <div key={String(label)} className="bg-card px-3 py-3">
                <Icon className="size-4 text-primary" />
                <p className="mt-2 text-[10px] text-secondary-text">{label}</p>
                <p className="mt-1 text-base font-semibold text-foreground">{value}</p>
              </div>
            ))}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-border px-4 py-2.5 text-[10px] text-secondary-text">
            <span>已结束运行样本：{formatCount(slo?.terminalRuns)}</span>
            <span>工具调用：{formatCount(workload?.toolCalls)}</span>
            <span>平均事件/运行：{numberValue(workload?.eventsPerRun)?.toFixed(1) ?? '—'}</span>
          </div>
        </Card>

        <Card padding="none" className="overflow-hidden rounded-xl">
          <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold text-foreground">执行缓存与事件落库</h2>
              <p className="mt-0.5 text-[11px] text-secondary-text">系统内部观测，不等同于运行记录内容</p>
            </div>
            <HardDrive className="size-4 text-primary" />
          </div>
          <div className="grid grid-cols-2 gap-px bg-border/70 sm:grid-cols-4 lg:grid-cols-2">
            {[
              ['缓存命中率', formatPercent(executionCache?.hitRate)],
              ['缓存命中', formatCount(executionCache?.hits)],
              ['事件批次', formatCount(persistence?.batches)],
              ['平均写入', persistenceBatches && persistenceBatches > 0 ? formatDuration(persistence?.averageWriteMs) : '—'],
            ].map(([label, value]) => (
              <div key={label} className="bg-card px-3 py-3">
                <p className="text-[10px] text-secondary-text">{label}</p>
                <p className="mt-1 text-sm font-semibold text-foreground">{value}</p>
              </div>
            ))}
          </div>
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <Card padding="none" className="overflow-hidden rounded-xl">
          <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold text-foreground">外部依赖探针</h2>
              <p className="mt-0.5 text-[11px] text-secondary-text">主动验证模型服务，不把上游状态伪装成静态配置</p>
            </div>
            <Network className="size-4 text-primary" />
          </div>
          {!dependencyChecks ? (
            <div className="flex items-center gap-2 px-4 py-5 text-xs text-secondary-text">
              <CircleAlert className="size-4 text-warning" />
              尚未执行深度依赖检查；点击右上角“执行依赖检查”后显示实时结果。
            </div>
          ) : (
            <div className="divide-y divide-border/70">
              {Object.entries(dependencyChecks).map(([name, checkValue]) => {
                const check = objectValue(checkValue);
                const ok = check?.ok === true;
                return (
                  <div key={name} className="flex items-center gap-3 px-4 py-3">
                    <StatusDot tone={ok ? 'success' : 'danger'} />
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-medium text-foreground">{DEPENDENCY_LABELS[name] ?? name}</p>
                      <p className="mt-0.5 truncate text-[10px] text-secondary-text" title={check?.error ? String(check.error) : undefined}>
                        {check?.error ? String(check.error) : ok ? '探针通过' : '探针失败'}
                      </p>
                    </div>
                    <Badge variant={ok ? 'success' : 'danger'}>{ok ? '正常' : '异常'}</Badge>
                  </div>
                );
              })}
              <div className="px-4 py-2 text-[10px] text-secondary-text">
                探针时间：{formatDate(dependencies?.probedAt)}{dependencies?.cached === true ? ' · 使用 60 秒缓存' : ''}
              </div>
            </div>
          )}
        </Card>

        <Card padding="none" className="overflow-hidden rounded-xl">
          <div className="border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold text-foreground">处理边界</h2>
            <p className="mt-0.5 text-[11px] text-secondary-text">系统监控和运行记录各自负责什么</p>
          </div>
          <div className="space-y-3 px-4 py-4 text-xs">
            <div className="flex gap-3">
              <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"><Monitor className="size-4" /></span>
              <div><p className="font-medium text-foreground">本页面</p><p className="mt-1 leading-5 text-secondary-text">发现服务不可用、容量不足、依赖熔断、事件落库和恢复机制异常。</p></div>
            </div>
            <div className="flex gap-3">
              <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-muted text-secondary-text"><Wrench className="size-4" /></span>
              <div><p className="font-medium text-foreground">运行记录</p><p className="mt-1 leading-5 text-secondary-text">进入某一次运行，核对工具请求、返回数据、证据和具体错误。</p></div>
            </div>
            <Link to="/setting?tab=runs" className="inline-flex items-center gap-1 text-primary hover:underline">
              需要排查单次运行时打开运行记录
              <ArrowRight className="size-3.5" />
            </Link>
          </div>
        </Card>
      </section>

      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border/70 bg-muted/25 px-3 py-2.5 text-[11px] text-secondary-text">
        <span className="flex items-center gap-1.5">
          <Clock3 className="size-3.5" />
          页面每 60 秒刷新一次浅层状态；深度依赖探针需要手动触发，并使用服务端 60 秒缓存。
        </span>
        <span>系统级监控不展示单次工具明细</span>
      </div>
    </div>
  );
};

export default AgentMonitoringPage;
