import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { agentMonitoringApi } from '../api/agentMonitoring';
import AgentMonitoringPage from './AgentMonitoringPage';

vi.mock('../api/agentMonitoring', () => ({
  agentMonitoringApi: {
    getMetrics: vi.fn(),
    getReadiness: vi.fn(),
  },
}));

const metrics = {
  checkedAt: '2026-08-14T10:00:00+08:00',
  metrics: {
    runs: { running: 1, failed: 2, completed: 8 },
    steps: [],
    expiredRunLeases: 1,
    activeResourceLeases: 2,
    openCircuits: 0,
    stepIdempotencyReuses: 0,
    recoveryAttempts24h: 1,
    recovery24h: { terminalRuns: 1, successfulRuns: 1, successRate: 1 },
    workload24h: {
      events: 42,
      eventsPerRun: 4.2,
      providerCalls: 12,
      toolCalls: 20,
      estimatedTokens: 5000,
      estimatedCostMicros: 100,
    },
    slo24h: {
      terminalRuns: 10,
      successfulRuns: 8,
      successRate: 0.8,
      durationMsP50: 1200,
      durationMsP95: 2400,
    },
  },
  alerts: [{
    code: 'agent_success_rate_below_slo',
    severity: 'critical',
    value: 0.8,
    threshold: 0.95,
  }],
  healthy: false,
};

const readiness = {
  status: 'ready',
  ready: true,
  checkedAt: '2026-08-14T10:00:00+08:00',
  checks: {
    database: { ok: true, schemaVersion: 'v1', expectedSchemaVersion: 'v1' },
    model: { ok: true, model: 'ai/glm-5.2[1m]' },
    runtime: {
      ok: true,
      engine: 'langgraph_agent_loop',
      workers: 1,
      activeRuns: 1,
      graphInitialized: true,
      checkpointer: 'sqlite',
      limits: { maxActiveRuns: 4, requestsPerMinute: 0 },
      eventPersistence: { batches: 2, batchFailures: 0, averageWriteMs: 8 },
      executionCache: { hits: 2, misses: 1, hitRate: 0.667 },
      issues: [],
    },
    tools: { ok: true, registered: 12, operationDirectory: 12 },
  },
};

describe('AgentMonitoringPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(agentMonitoringApi.getMetrics).mockResolvedValue(metrics);
    vi.mocked(agentMonitoringApi.getReadiness).mockResolvedValue(readiness);
  });

  it('shows system-level anomalies without duplicating run/tool detail', async () => {
    render(
      <MemoryRouter>
        <AgentMonitoringPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: '系统运行监控' })).toBeInTheDocument();
    expect(screen.getByText('系统完成率低于 SLO')).toBeInTheDocument();
    expect(screen.getByText('实际 80% · 阈值 95%')).toBeInTheDocument();
    expect(screen.getByText('ai/glm-5.2[1m]')).toBeInTheDocument();
    expect(screen.getByText('系统异常')).toBeInTheDocument();
    expect(screen.queryByText('当前运行分布')).not.toBeInTheDocument();
    expect(screen.queryByText('工具步骤健康度')).not.toBeInTheDocument();

    const alertLink = screen.getByRole('link', { name: /系统完成率低于 SLO/ });
    expect(alertLink).toHaveAttribute('href', '/setting?tab=runs&status=failed');
  });

  it('allows a manual deep dependency probe', async () => {
    render(
      <MemoryRouter>
        <AgentMonitoringPage embedded />
      </MemoryRouter>,
    );

    await screen.findByRole('heading', { name: '系统运行监控' });
    const probeButton = screen.getByRole('button', { name: '执行依赖检查' });
    await waitFor(() => expect(probeButton).not.toBeDisabled());
    vi.mocked(agentMonitoringApi.getReadiness).mockResolvedValueOnce({
      ...readiness,
      checks: {
        ...readiness.checks,
        dependencies: {
          ok: true,
          cached: false,
          probedAt: '2026-08-14T10:01:00+08:00',
          checks: {
            modelProvider: { ok: true },
            toolCatalog: { ok: true },
          },
        },
      },
    });

    fireEvent.click(probeButton);

    await waitFor(() => expect(screen.getByText('模型服务')).toBeInTheDocument());
    expect(agentMonitoringApi.getReadiness).toHaveBeenLastCalledWith({ deep: true });
    expect(screen.getAllByText('探针通过')).toHaveLength(2);
  });

  it('surfaces a failed readiness check as a system issue', async () => {
    const notReadyReadiness = {
      ...readiness,
      status: 'not_ready',
      ready: false,
      checks: {
        ...readiness.checks,
        model: { ok: false, error: '模型服务不可用' },
      },
    };
    vi.mocked(agentMonitoringApi.getReadiness).mockResolvedValueOnce(notReadyReadiness);

    render(
      <MemoryRouter>
        <AgentMonitoringPage embedded />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText('控制面存在未通过检查')).toBeInTheDocument());
    expect(screen.getByText('模型服务不可用')).toBeInTheDocument();
    expect(screen.getByText('严重异常')).toBeInTheDocument();
  });
});
