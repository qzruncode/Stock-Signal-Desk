import { renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useTaskStream } from '../useTaskStream';

const { getTaskStreamUrl } = vi.hoisted(() => ({
  getTaskStreamUrl: vi.fn(() => 'http://localhost/api/v1/analysis/tasks/stream'),
}));

vi.mock('../../api/analysis', () => ({
  analysisApi: {
    getTaskStreamUrl,
  },
}));

type MockEventSourceInstance = {
  listeners: Record<string, ((event: MessageEvent<string>) => void) | undefined>;
  addEventListener: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  onerror: ((event: Event) => void) | null;
};

describe('useTaskStream', () => {
  let eventSourceInstance: MockEventSourceInstance;

  beforeEach(() => {
    vi.clearAllMocks();

    class MockEventSource {
      listeners: MockEventSourceInstance['listeners'] = {};
      addEventListener = vi.fn((type: string, listener: (event: MessageEvent<string>) => void) => {
        this.listeners[type] = listener;
      });
      close = vi.fn();
      onerror: ((event: Event) => void) | null = null;

      constructor(...args: unknown[]) {
        void args;
        // eslint-disable-next-line @typescript-eslint/no-this-alias
        eventSourceInstance = this;
      }
    }

    Object.defineProperty(window, 'EventSource', {
      writable: true,
      configurable: true,
      value: MockEventSource,
    });
  });

  it('closes the SSE connection when the hook unmounts', () => {
    const { unmount } = renderHook(() => useTaskStream({ enabled: true }));

    expect(getTaskStreamUrl).toHaveBeenCalledTimes(1);

    unmount();

    expect(eventSourceInstance.close).toHaveBeenCalled();
  });

  it('parses task_progress events and forwards the updated task payload', () => {
    const onTaskProgress = vi.fn();

    renderHook(() => useTaskStream({ enabled: true, onTaskProgress }));

    eventSourceInstance.listeners.task_progress?.(
      new MessageEvent('task_progress', {
        data: JSON.stringify({
          task_id: 'task-1',
          stock_code: '600519',
          stock_name: '贵州茅台',
          status: 'processing',
          progress: 72,
          message: 'LLM 正在生成分析结果',
          report_type: 'detailed',
          created_at: '2026-03-29T08:00:00Z',
        }),
      }),
    );

    expect(onTaskProgress).toHaveBeenCalledWith({
      taskId: 'task-1',
      stockCode: '600519',
      stockName: '贵州茅台',
      status: 'processing',
      progress: 72,
      message: 'LLM 正在生成分析结果',
      reportType: 'detailed',
      createdAt: '2026-03-29T08:00:00Z',
      startedAt: undefined,
      completedAt: undefined,
      error: undefined,
      originalQuery: undefined,
      selectionSource: undefined,
    });
  });

  it('schedules only one reconnect while repeated errors arrive before the timer fires', () => {
    vi.useFakeTimers();

    renderHook(() => useTaskStream({ enabled: true, reconnectDelay: 3000 }));

    const firstInstance = eventSourceInstance;

    firstInstance.onerror?.(new Event('error'));
    firstInstance.onerror?.(new Event('error'));

    expect(firstInstance.close).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(2999);
    expect(getTaskStreamUrl).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(1);
    expect(getTaskStreamUrl).toHaveBeenCalledTimes(2);

    vi.useRealTimers();
  });
});
