import { beforeEach, describe, expect, it, vi } from 'vitest';
import { batchApi } from '../../api/batch';
import { useBatchStore } from '../batchStore';

vi.mock('../../api/batch', () => ({
  batchApi: {
    triggerRun: vi.fn(),
    getCurrentProgress: vi.fn(),
    getRuns: vi.fn(),
    getRunReport: vi.fn(),
    getSchedule: vi.fn(),
    updateSchedule: vi.fn(),
  },
}));

vi.mock('../../api/prompts', () => ({
  promptsApi: {
    getPromptTemplates: vi.fn(),
  },
}));

describe('batchStore', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    useBatchStore.setState({
      isRunning: false,
      runStockCount: 0,
      runCompleted: 0,
      runSuccess: 0,
      runFailed: 0,
      currentStock: null,
      currentMessage: null,
      runs: [],
      error: null,
    });
  });

  it('maps backend progress fields into visible run state', async () => {
    vi.mocked(batchApi.getCurrentProgress).mockResolvedValue({
      running: true,
      state: {
        run_id: 'run-1',
        total: 2,
        completed: 1,
        success: 1,
        failed: 0,
        current_stock: '贵州茅台(600519)',
        current_message: '贵州茅台(600519)：已接收 120 字，仍在生成...',
      },
    });

    const stop = useBatchStore.getState().pollProgress();
    await vi.runOnlyPendingTimersAsync();

    const state = useBatchStore.getState();
    expect(state.runCompleted).toBe(1);
    expect(state.runSuccess).toBe(1);
    expect(state.runFailed).toBe(0);
    expect(state.currentStock).toBe('贵州茅台(600519)');
    expect(state.currentMessage).toContain('已接收 120 字');

    stop();
  });

  it('restores visible progress after a page refresh', async () => {
    vi.mocked(batchApi.getCurrentProgress).mockResolvedValue({
      running: true,
      state: {
        run_id: 'run-1',
        total: 347,
        completed: 57,
        success: 57,
        failed: 0,
        current_stock: '300820',
        current_message: '300820：已接收 1447 字，仍在生成...',
      },
    });

    const restored = await useBatchStore.getState().syncCurrentProgress();

    const state = useBatchStore.getState();
    expect(restored).toBe(true);
    expect(state.isRunning).toBe(true);
    expect(state.runStockCount).toBe(347);
    expect(state.runCompleted).toBe(57);
    expect(state.currentMessage).toContain('1447');
  });
});
