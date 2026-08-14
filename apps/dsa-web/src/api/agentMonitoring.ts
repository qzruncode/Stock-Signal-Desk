import apiClient from './index';
import { toCamelCase } from './utils';

export interface AgentStepMetric {
  toolName: string;
  status: string;
  count: number;
}

export interface AgentRuntimeMetrics {
  runs: Record<string, number>;
  steps: AgentStepMetric[];
  expiredRunLeases: number;
  activeResourceLeases: number;
  openCircuits: number;
  stepIdempotencyReuses: number;
  recoveryAttempts24h: number;
  recovery24h: {
    terminalRuns: number;
    successfulRuns: number;
    successRate?: number | null;
  };
  workload24h: {
    events: number;
    eventsPerRun: number;
    providerCalls: number;
    toolCalls: number;
    estimatedTokens: number;
    estimatedCostMicros: number;
  };
  slo24h: {
    terminalRuns: number;
    successfulRuns: number;
    successRate?: number | null;
    durationMsP50?: number | null;
    durationMsP95?: number | null;
    planningMsP50?: number | null;
    planningMsP95?: number | null;
  };
}

export interface AgentMetricAlert {
  code: string;
  severity: 'critical' | 'warning' | 'info' | string;
  value?: number | null;
  threshold?: number | null;
}

export interface AgentMetricsResponse {
  checkedAt: string;
  metrics: AgentRuntimeMetrics;
  alerts: AgentMetricAlert[];
  healthy: boolean;
}

export interface AgentReadinessCheck {
  ok?: boolean;
  [key: string]: unknown;
}

export interface AgentReadinessResponse {
  status: 'ready' | 'not_ready' | string;
  ready: boolean;
  checkedAt: string;
  checks: Record<string, AgentReadinessCheck>;
}

const normalizeMetricsResponse = (response: AgentMetricsResponse): AgentMetricsResponse => {
  // camelcase-keys preserves the digit boundary as `slo24H` for keys such as
  // `slo_24h`. Keep the UI contract lower-camel-case at this API boundary.
  const rawMetrics = response.metrics as AgentRuntimeMetrics & Record<string, unknown>;
  return {
    ...response,
    metrics: {
      ...response.metrics,
      slo24h: (rawMetrics.slo24h ?? rawMetrics.slo24H) as AgentRuntimeMetrics['slo24h'],
      workload24h: (rawMetrics.workload24h ?? rawMetrics.workload24H) as AgentRuntimeMetrics['workload24h'],
      recovery24h: (rawMetrics.recovery24h ?? rawMetrics.recovery24H) as AgentRuntimeMetrics['recovery24h'],
    },
  };
};

export const agentMonitoringApi = {
  async getMetrics(): Promise<AgentMetricsResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/agent/metrics');
    return normalizeMetricsResponse(toCamelCase<AgentMetricsResponse>(response.data));
  },

  async getReadiness(options: { deep?: boolean } = {}): Promise<AgentReadinessResponse> {
    // Readiness intentionally returns 503 when the snapshot is unhealthy. It
    // is still useful to the monitor, so preserve and render that response.
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/agent/readiness', {
      ...(options.deep ? { params: { deep: true } } : {}),
      validateStatus: (status) => status >= 200 && status < 600,
    });
    return toCamelCase<AgentReadinessResponse>(response.data);
  },
};
