import { describe, expect, it } from 'vitest';
import type { TaskInfo } from '../../types/analysis';
import { isFloatingAnalysisTaskVisible } from '../analysisTaskVisibility';

function task(overrides: Partial<TaskInfo> = {}): TaskInfo {
  return {
    taskId: 'task-1',
    stockCode: '000001',
    status: 'processing',
    progress: 24,
    reportType: 'detailed',
    createdAt: '2026-07-28T10:00:00+08:00',
    ...overrides,
  };
}

describe('isFloatingAnalysisTaskVisible', () => {
  it('keeps standalone stock analysis in the global task panel', () => {
    expect(isFloatingAnalysisTaskVisible(task())).toBe(true);
  });

  it('does not split an integrated market-mainline workflow into a float', () => {
    expect(isFloatingAnalysisTaskVisible(task({
      stockCode: 'MARKET_MAINLINE',
      reportType: 'market_mainline_report',
    }))).toBe(false);
  });
});
