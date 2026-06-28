import { getParsedApiError } from '../../api/error';
import type { TaskInfo } from '../../types/analysis';
import type { StoreGet, StoreSet } from '../types';

const dismissedTaskIds = new Set<string>();

export interface TaskSlice {
  syncTaskCreated: (task: TaskInfo) => void;
  syncTaskUpdated: (task: TaskInfo) => void;
  syncTaskFailed: (task: TaskInfo) => void;
  removeTask: (taskId: string) => void;
  autoSelectByStockCode: (stockCode: string) => Promise<void>;
}

export function resetTaskDismissed() {
  dismissedTaskIds.clear();
}

function normalizeStockIdentity(value?: string | null): string {
  return (value || '')
    .trim()
    .toUpperCase()
    .replace(/^(SH|SZ|BJ|HK)/, '')
    .replace(/\.(SH|SZ|SS|BJ|HK|US)$/, '');
}

export function createTaskSlice(get: StoreGet, set: StoreSet): TaskSlice {
  return {
    syncTaskCreated: (task) => {
      if (dismissedTaskIds.has(task.taskId)) {
        return;
      }
      if (get().activeTasks.some((item) => item.taskId === task.taskId)) {
        return;
      }
      set({ activeTasks: [...get().activeTasks, task] });
    },

    syncTaskUpdated: (task) => {
      if (dismissedTaskIds.has(task.taskId)) {
        return;
      }
      const nextTasks = [...get().activeTasks];
      const index = nextTasks.findIndex((item) => item.taskId === task.taskId);
      if (index >= 0) {
        nextTasks[index] = task;
        set({ activeTasks: nextTasks });
      }
    },

    syncTaskFailed: (task) => {
      get().syncTaskUpdated(task);
      set({
        error: getParsedApiError(task.error || '分析失败'),
        pendingAutoSelectCode: get().pendingAutoSelectCode === task.stockCode ? null : get().pendingAutoSelectCode,
      });
    },

    removeTask: (taskId) => {
      dismissedTaskIds.add(taskId);
      set({ activeTasks: get().activeTasks.filter((task) => task.taskId !== taskId) });
    },

    autoSelectByStockCode: async (stockCode) => {
      const { historyItems, selectHistoryItem } = get();
      const target = normalizeStockIdentity(stockCode);
      const match = historyItems.find((item) => normalizeStockIdentity(item.stockCode) === target);
      if (match) {
        await selectHistoryItem(match.id);
      }
      set({ pendingAutoSelectCode: null });
    },
  };
}
