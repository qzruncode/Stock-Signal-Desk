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
  pollProgress: () => () => void; // Returns stop function
  fetchRuns: () => Promise<void>;
  viewReport: (runId: string) => Promise<void>;
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
      });
      return true;
    } catch (err) {
      set({ error: getParsedApiError(err) });
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
          set({ isRunning: false });
          if (timer) {
            clearInterval(timer);
            timer = null;
          }
          get().fetchRuns();
          return;
        }

        if (progress.state) {
          set({
            runCompleted: (progress.state.completed as number) || 0,
            runSuccess: (progress.state.success as number) || 0,
            runFailed: (progress.state.failed as number) || 0,
            currentStock: (progress.state.current_stock as string) || null,
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
