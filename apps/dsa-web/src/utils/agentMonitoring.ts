import type { AgentMetricAlert, AgentMetricsResponse, AgentReadinessCheck, AgentReadinessResponse } from '../api/agentMonitoring';
import { formatDateTime } from './format';

export type SignalSeverity = 'critical' | 'warning' | 'info';
export type StatusTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral';

export interface SystemSignal {
  id: string;
  severity: SignalSeverity;
  title: string;
  description: string;
  detail?: string;
  href?: string;
}

export const READINESS_LABELS: Record<string, string> = {
  database: '数据库连接',
  model: '模型配置',
  runtime: 'Agent 运行时',
  tools: '工具目录',
  dependencies: '外部依赖探针',
};

export const DEPENDENCY_LABELS: Record<string, string> = {
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

export const numberValue = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return null;
};

export const countValue = (value: unknown): number => numberValue(value) ?? 0;

export const objectValue = (value: unknown): Record<string, unknown> | null => (
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
);

export const formatCount = (value: unknown): string => {
  const number = numberValue(value);
  return number == null ? '—' : new Intl.NumberFormat('zh-CN').format(number);
};

export const formatPercent = (value: unknown): string => {
  const number = numberValue(value);
  return number == null ? '—' : `${Math.round(number * 100)}%`;
};

export const formatDuration = (value: unknown): string => {
  const number = numberValue(value);
  if (number == null) return '—';
  if (number < 1000) return `${Math.round(number)} ms`;
  if (number < 60_000) return `${(number / 1000).toFixed(1)} 秒`;
  return `${Math.floor(number / 60_000)} 分 ${Math.round((number % 60_000) / 1000)} 秒`;
};

export const formatDate = (value: unknown): string => (
  typeof value === 'string' && value ? formatDateTime(value) : '—'
);

export const severityTone = (severity: SignalSeverity): StatusTone => {
  if (severity === 'critical') return 'danger';
  if (severity === 'warning') return 'warning';
  return 'info';
};

export const severityLabel = (severity: SignalSeverity): string => {
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

export const readinessDetail = (name: string, check: AgentReadinessCheck): string => {
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

export const buildSystemSignals = (
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
