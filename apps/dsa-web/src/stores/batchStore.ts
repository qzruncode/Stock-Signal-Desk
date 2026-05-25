import { create } from 'zustand';
import { batchApi, type BatchRunItem, type BatchSchedule } from '../api/batch';
import { promptsApi, type PromptTemplateItem } from '../api/prompts';
import { getParsedApiError, type ParsedApiError } from '../api/error';

interface BatchState {
  // Prompt templates
  templates: PromptTemplateItem[];
  selectedTemplateId: string;
  isLoadingTemplates: boolean;
  templatesError: string | null;

  // Batch run state
  isRunning: boolean;
  runStockCount: number;
  runCompleted: number;
  runSuccess: number;
  runFailed: number;
  currentStock: string | null;
  currentMessage: string | null;
  runStatus: string | null;

  // Batch history
  runs: BatchRunItem[];
  isLoadingRuns: boolean;

  // Selected report
  selectedReportContent: string | null;
  selectedReportRunId: string | null;
  isLoadingReport: boolean;

  // Schedule
  schedule: BatchSchedule | null;
  isLoadingSchedule: boolean;
  scheduleError: string | null;

  // Error
  error: ParsedApiError | null;

  // Actions
  loadTemplates: () => Promise<void>;
  setSelectedTemplateId: (id: string) => void;
  triggerBatchRun: (stockCodes: string[]) => Promise<boolean>;
  resumeBatchRun: (runId: string, stockCodes: string[]) => Promise<boolean>;
  pauseBatchRun: () => Promise<boolean>;
  continueBatchRun: () => Promise<boolean>;
  stopBatchRun: () => Promise<boolean>;
  syncCurrentProgress: () => Promise<boolean>;
  pollProgress: () => () => void; // Returns stop function
  fetchRuns: () => Promise<void>;
  viewReport: (runId: string) => Promise<void>;
  deleteRun: (runId: string) => Promise<boolean>;
  closeReport: () => void;
  fetchSchedule: () => Promise<void>;
  updateSchedule: (data: { enabled: boolean; times: string[]; template_id: string }) => Promise<void>;
  clearError: () => void;
}

export const useBatchStore = create<BatchState>((set, get) => ({
  templates: [],
  selectedTemplateId: '',
  isLoadingTemplates: false,
  templatesError: null,

  isRunning: false,
  runStockCount: 0,
  runCompleted: 0,
  runSuccess: 0,
  runFailed: 0,
  currentStock: null,
  currentMessage: null,
  runStatus: null,

  runs: [],
  isLoadingRuns: false,

  selectedReportContent: null,
  selectedReportRunId: null,
  isLoadingReport: false,

  schedule: null,
  isLoadingSchedule: false,
  scheduleError: null,

  error: null,

  loadTemplates: async () => {
    set({ isLoadingTemplates: true, templatesError: null });
    try {
      const templates = await promptsApi.getPromptTemplates();
      const selectedTemplateId = get().selectedTemplateId;
      const defaultTemplate = templates.find((t) => t.is_default);
      set({
        templates,
        isLoadingTemplates: false,
        selectedTemplateId: selectedTemplateId || defaultTemplate?.id || templates[0]?.id || '',
      });
    } catch (err) {
      set({
        templatesError: err instanceof Error ? err.message : '加载模板失败',
        isLoadingTemplates: false,
      });
    }
  },

  setSelectedTemplateId: (id) => set({ selectedTemplateId: id }),

  triggerBatchRun: async (stockCodes) => {
    const { selectedTemplateId, loadTemplates } = get();
    let templateId = selectedTemplateId;

    if (!templateId) {
      await loadTemplates();
      templateId = get().selectedTemplateId;
    }

    if (!templateId) {
      set({ error: { title: '缺少模板', message: '请先选择提示词模板', rawMessage: '请先选择提示词模板', category: 'missing_params' } });
      return false;
    }

    if (stockCodes.length === 0) {
      set({ error: { title: '自选股为空', message: '自选股列表为空，请先在设置中添加自选股', rawMessage: '自选股列表为空', category: 'missing_params' } });
      return false;
    }

    try {
      const result = await batchApi.triggerRun({
        stock_codes: stockCodes,
        template_id: templateId,
      });
      set({
        error: null,
        isRunning: true,
        runStockCount: result.stock_count,
        runCompleted: 0,
        runSuccess: 0,
        runFailed: 0,
        currentStock: null,
        currentMessage: '准备中...',
        runStatus: 'running',
      });
      return true;
    } catch (err) {
      set({ error: getParsedApiError(err) });
      return false;
    }
  },

  resumeBatchRun: async (runId, stockCodes) => {
    try {
      const result = await batchApi.resumeRun(runId, stockCodes);
      set({
        error: null,
        isRunning: true,
        runStockCount: result.stock_count,
        runCompleted: Math.max(0, result.stock_count - result.pending_count),
        runSuccess: 0,
        runFailed: 0,
        currentStock: null,
        currentMessage: result.pending_count > 0 ? '准备续跑...' : '正在生成报告...',
        runStatus: 'running',
      });
      return true;
    } catch (err) {
      set({ error: getParsedApiError(err) });
      return false;
    }
  },

  pauseBatchRun: async () => {
    try {
      await batchApi.pauseCurrentRun();
      set({ runStatus: 'paused', currentMessage: '已暂停：正在执行中的请求会先收尾' });
      return true;
    } catch (err) {
      set({ error: getParsedApiError(err) });
      return false;
    }
  },

  continueBatchRun: async () => {
    try {
      await batchApi.resumeCurrentRun();
      set({ runStatus: 'running', currentMessage: '继续跑批中...' });
      return true;
    } catch (err) {
      set({ error: getParsedApiError(err) });
      return false;
    }
  },

  stopBatchRun: async () => {
    try {
      await batchApi.stopCurrentRun();
      set({ runStatus: 'stopping', currentMessage: '正在终止：已开始的请求会先收尾' });
      return true;
    } catch (err) {
      set({ error: getParsedApiError(err) });
      return false;
    }
  },

  syncCurrentProgress: async () => {
    try {
      const progress = await batchApi.getCurrentProgress();
      if (!progress.running || !progress.state) {
        return false;
      }
      set({
        isRunning: true,
        runStockCount: (progress.state.total as number) || get().runStockCount,
        runCompleted: (progress.state.completed as number) || 0,
        runSuccess: (progress.state.success as number) || 0,
        runFailed: (progress.state.failed as number) || 0,
        currentStock: (progress.state.current_stock as string) || null,
        currentMessage: (progress.state.current_message as string) || null,
        runStatus: (progress.state.status as string) || 'running',
      });
      return true;
    } catch {
      return false;
    }
  },

  pollProgress: () => {
    let active = true;
    let timer: ReturnType<typeof setInterval> | null = null;

    const poll = async () => {
      if (!active) return;
      try {
        const progress = await batchApi.getCurrentProgress();
        if (!active) return;

        if (!progress.running) {
          if (progress.state) {
            set({
              isRunning: false,
              runStockCount: (progress.state.total as number) || get().runStockCount,
              runCompleted: (progress.state.completed as number) || 0,
              runSuccess: (progress.state.success as number) || 0,
              runFailed: (progress.state.failed as number) || 0,
              currentStock: (progress.state.current_stock as string) || null,
              currentMessage: (progress.state.current_message as string) || null,
              runStatus: (progress.state.status as string) || null,
            });
          } else {
            set({ isRunning: false, currentStock: null, currentMessage: null, runStatus: null });
          }
          if (timer) {
            clearInterval(timer);
            timer = null;
          }
          get().fetchRuns();
          return;
        }

        if (progress.state) {
          set({
            runStockCount: (progress.state.total as number) || get().runStockCount,
            runCompleted: (progress.state.completed as number) || 0,
            runSuccess: (progress.state.success as number) || 0,
            runFailed: (progress.state.failed as number) || 0,
            currentStock: (progress.state.current_stock as string) || null,
            currentMessage: (progress.state.current_message as string) || null,
            runStatus: (progress.state.status as string) || 'running',
          });
        }
      } catch {
        // Silently ignore poll errors
      }
    };

    void poll();
    timer = setInterval(poll, 2000);

    return () => {
      active = false;
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
    };
  },

  deleteRun: async (runId) => {
    try {
      await batchApi.deleteRun(runId);
      set((state) => ({
        runs: state.runs.filter((run) => run.run_id !== runId),
        selectedReportContent: state.selectedReportRunId === runId ? null : state.selectedReportContent,
        selectedReportRunId: state.selectedReportRunId === runId ? null : state.selectedReportRunId,
        error: null,
      }));
      return true;
    } catch (err) {
      set({ error: getParsedApiError(err) });
      return false;
    }
  },

  fetchRuns: async () => {
    set({ isLoadingRuns: true });
    try {
      const runs = await batchApi.getRuns(20);
      set({ runs, isLoadingRuns: false });
    } catch {
      set({ isLoadingRuns: false });
    }
  },

  viewReport: async (runId) => {
    set({ isLoadingReport: true, selectedReportRunId: runId });
    try {
      const content = await batchApi.getRunReport(runId);
      set({ selectedReportContent: content, isLoadingReport: false });
    } catch (err) {
      set({ error: getParsedApiError(err), isLoadingReport: false });
    }
  },

  closeReport: () => set({ selectedReportContent: null, selectedReportRunId: null }),

  fetchSchedule: async () => {
    set({ isLoadingSchedule: true });
    try {
      const schedule = await batchApi.getSchedule();
      set({ schedule, isLoadingSchedule: false });
    } catch {
      set({ isLoadingSchedule: false });
    }
  },

  updateSchedule: async (data) => {
    try {
      const schedule = await batchApi.updateSchedule(data);
      set({ schedule, scheduleError: null });
    } catch (err) {
      set({ scheduleError: err instanceof Error ? err.message : '保存失败' });
    }
  },

  clearError: () => set({ error: null }),
}));
