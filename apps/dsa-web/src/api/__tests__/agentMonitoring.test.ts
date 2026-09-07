import { beforeEach, describe, expect, it, vi } from 'vitest';
import { agentMonitoringApi } from '../agentMonitoring';

const get = vi.hoisted(() => vi.fn());

vi.mock('../index', () => ({
  default: { get },
}));

describe('agentMonitoringApi', () => {
  beforeEach(() => {
    get.mockReset();
  });

  it('normalizes underscored 24h metric groups for the monitoring page', async () => {
    get.mockResolvedValueOnce({
      data: {
        checked_at: '2026-08-14T10:00:00+08:00',
        metrics: {
          runs: { completed: 2 },
          steps: [],
          expired_run_leases: 0,
          active_resource_leases: 0,
          open_circuits: 0,
          half_open_circuits: 0,
          expired_circuits: 0,
          step_idempotency_reuses: 0,
          recovery_attempts_24h: 1,
          recovery_24h: { terminal_runs: 1, successful_runs: 1, success_rate: 1 },
          workload_24h: { events: 2, events_per_run: 1, provider_calls: 1, tool_calls: 1,
            actual_usage: { input_tokens: 10, output_tokens: 5, total_tokens: 15, reported_calls: 1, unreported_calls: 0 } },
          slo_24h: {
            terminal_runs: 2,
            successful_runs: 2,
            success_rate: 1,
            duration_ms_p50: 100,
            duration_ms_p95: 200,
          },
        },
        alerts: [],
        healthy: true,
      },
    });

    const result = await agentMonitoringApi.getMetrics();

    expect(get).toHaveBeenCalledWith('/api/v1/agent/metrics');
    expect(result.metrics.slo24h.durationMsP95).toBe(200);
    expect(result.metrics.workload24h.providerCalls).toBe(1);
    expect(result.metrics.workload24h.actualUsage?.totalTokens).toBe(15);
    expect(result.metrics.recovery24h.successRate).toBe(1);
  });

  it('keeps a not-ready readiness response available to the UI', async () => {
    get.mockResolvedValueOnce({
      status: 503,
      data: {
        status: 'not_ready',
        ready: false,
        checked_at: '2026-08-14T10:00:00+08:00',
        checks: { model: { ok: false, error: '模型服务不可用' } },
      },
    });

    const result = await agentMonitoringApi.getReadiness();

    expect(get).toHaveBeenCalledWith('/api/v1/agent/readiness', expect.objectContaining({
      validateStatus: expect.any(Function),
    }));
    expect(result.ready).toBe(false);
    expect(result.checks.model.error).toBe('模型服务不可用');
  });
});
