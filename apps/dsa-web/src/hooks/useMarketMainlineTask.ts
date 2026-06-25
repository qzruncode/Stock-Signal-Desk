import { useEffect, useMemo, useState } from 'react';
import {
  marketThemesApi,
  type MarketMainlineReportResponse,
} from '../api/marketThemes';
import { analysisApi } from '../api/analysis';
import { useTaskStream } from './useTaskStream';
import {
  hasReadyReport,
  readTaskPayload,
  mergeDraftIntoReport,
  mergeTaskDraft,
  hasStructuredContent,
  detectStreamKind,
  formatStreamText,
  type MarketMainlineTaskPayload,
} from '../utils/marketMainlineFormat';

const ACTIVE_TASK_STORAGE_KEY = 'market-mainline-active-task-id';

function rememberActiveTask(taskId: string | null) {
  if (taskId) {
    window.localStorage.setItem(ACTIVE_TASK_STORAGE_KEY, taskId);
  } else {
    window.localStorage.removeItem(ACTIVE_TASK_STORAGE_KEY);
  }
}

export interface UseMarketMainlineTaskReturn {
  report: MarketMainlineReportResponse | null;
  loading: boolean;
  submitting: boolean;
  error: string | null;
  taskError: string | null;
  streamText: string;
  streamPhase: string | null;
  taskMessage: string | null;
  isGenerating: boolean;
  displayReport: MarketMainlineReportResponse | null;
  renderReport: MarketMainlineReportResponse | null;
  displayText: string;
  streamKind: 'json' | 'report';
  formattedDisplayText: string;
  streamStatsText: string;
  debugInput: MarketMainlineReportResponse['debug_input'] | undefined;
  reportDraft: Partial<MarketMainlineReportResponse> | null;
  loadLatest: () => Promise<MarketMainlineReportResponse | null>;
  startGeneration: (force?: boolean) => Promise<void>;
}

export function useMarketMainlineTask(): UseMarketMainlineTaskReturn {
  const [report, setReport] = useState<MarketMainlineReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [taskError, setTaskError] = useState<string | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [streamText, setStreamText] = useState('');
  const [reportDraft, setReportDraft] = useState<Partial<MarketMainlineReportResponse> | null>(null);
  const [debugInput, setDebugInput] = useState<MarketMainlineReportResponse['debug_input'] | undefined>(undefined);
  const [streamPhase, setStreamPhase] = useState<string | null>(null);
  const [taskMessage, setTaskMessage] = useState<string | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);

  const clearStreamingDisplay = () => {
    setReport(null);
    setStreamText('');
    setReportDraft(null);
    setDebugInput(undefined);
  };

  const updateStreamText = (nextText?: string) => {
    if (typeof nextText !== 'string') {
      return;
    }
    setStreamText((current) => (nextText.length >= current.length ? nextText : current));
  };

  const updateReportDraft = (nextDraft?: Partial<MarketMainlineReportResponse>) => {
    if (!nextDraft) {
      return;
    }
    setReportDraft((current) => mergeTaskDraft(current, nextDraft));
  };

  const loadLatest = async (): Promise<MarketMainlineReportResponse | null> => {
    setLoading(true);
    setError(null);
    try {
      const response = await marketThemesApi.getReport();
      if (hasReadyReport(response)) {
        setReport(response);
        setDebugInput(response.debug_input);
        setTaskError(null);
        setStreamPhase(null);
        setTaskMessage(null);
        setStreamText('');
        setReportDraft(null);
        clearActiveTask();
      } else {
        clearStreamingDisplay();
      }
      return response;
    } catch {
      setError('市场主线报告加载失败。');
      return null;
    } finally {
      setLoading(false);
    }
  };

  const applyTaskState = (
    payload: MarketMainlineTaskPayload,
    message: string | null,
    taskId: string,
    status: 'pending' | 'processing' | 'completed' | 'failed',
  ) => {
    if (status === 'pending' || status === 'processing') {
      setReport(null);
    }
    setActiveTaskId(taskId);
    rememberActiveTask(taskId);
    setIsGenerating(status === 'pending' || status === 'processing');
    setTaskMessage(message);
    setStreamPhase(payload.phase ?? (status === 'pending' ? 'queued' : status));
    updateStreamText(payload.stream_text);
    updateReportDraft(payload.report_draft);
    if (payload.debug_input) {
      setDebugInput(payload.debug_input);
    }
    if (payload.report) {
      setReport(payload.report);
      setDebugInput(payload.report.debug_input);
    }
  };

  const clearActiveTask = () => {
    setActiveTaskId(null);
    rememberActiveTask(null);
    setIsGenerating(false);
  };

  const hydrateTaskStatus = async (taskId: string): Promise<boolean> => {
    try {
      const status = await analysisApi.getStatus(taskId);
      const payload = readTaskPayload(status.result?.report);

      if (status.status === 'pending' || status.status === 'processing') {
        applyTaskState(
          payload,
          payload.phase ? `任务进行中：${payload.phase}` : '任务进行中',
          taskId,
          status.status,
        );
        return true;
      }

      if (status.status === 'completed') {
        if (payload.report) {
          setReport(payload.report);
          setDebugInput(payload.report.debug_input);
        }
        setTaskError(null);
        setStreamPhase('completed');
        setTaskMessage('模型研判生成完成');
        clearActiveTask();
        void loadLatest();
        return false;
      }

      setTaskError(status.error || '模型研判生成失败。');
      setStreamPhase('failed');
      setTaskMessage(status.error || '模型研判生成失败');
      clearActiveTask();
      return false;
    } catch {
      setTaskMessage('任务状态恢复中');
      return false;
    }
  };

  const resumeActiveTask = async (): Promise<boolean> => {
    const rememberedTaskId = window.localStorage.getItem(ACTIVE_TASK_STORAGE_KEY);
    if (rememberedTaskId) {
      setActiveTaskId(rememberedTaskId);
      setIsGenerating(true);
      setStreamPhase('reconnecting');
      setTaskMessage('正在恢复进行中的任务');
      const resumed = await hydrateTaskStatus(rememberedTaskId);
      if (resumed) {
        return true;
      }
    }

    try {
      const tasksResponse = await analysisApi.getTasks({ status: 'pending,processing', limit: 20 });
      const match = tasksResponse.tasks.find(
        (task) => task.stockCode === 'MARKET_MAINLINE' && task.reportType === 'market_mainline_report',
      );
      if (!match) {
        return false;
      }
      return hydrateTaskStatus(match.taskId);
    } catch {
      return false;
    }
  };

  const startGeneration = async (force: boolean = true) => {
    setSubmitting(true);
    setError(null);
    setTaskError(null);
    clearStreamingDisplay();
    setStreamPhase('queued');
    setTaskMessage('任务已提交，等待服务端开始处理');
    try {
      const task = await marketThemesApi.createReportTask(force);
      setActiveTaskId(task.task_id);
      rememberActiveTask(task.task_id);
      setIsGenerating(true);
    } catch (error) {
      const message = error instanceof Error && error.message ? error.message : '模型研判任务提交失败。';
      setTaskError(message);
      setIsGenerating(false);
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    let active = true;
    const initialize = async () => {
      const resumed = await resumeActiveTask();
      if (!active) {
        return;
      }
      if (resumed) {
        return;
      }
      const latest = await loadLatest();
      if (!active) {
        return;
      }
      if (hasReadyReport(latest)) {
        return;
      }
      if (latest?.report_pending === true) {
        void startGeneration(false);
      }
    };
    void initialize();
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!activeTaskId) {
      return;
    }
    const intervalId = window.setInterval(() => {
      void hydrateTaskStatus(activeTaskId);
    }, 5000);
    return () => window.clearInterval(intervalId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTaskId]);

  useTaskStream({
    enabled: Boolean(activeTaskId),
    onTaskStarted: (task) => {
      if (task.taskId !== activeTaskId) return;
      setIsGenerating(true);
      setStreamPhase('started');
      setTaskMessage(task.message || '任务已启动');
    },
    onTaskProgress: (task) => {
      if (task.taskId !== activeTaskId) return;
      const payload = readTaskPayload(task.result);
      setIsGenerating(true);
      setStreamPhase(payload.phase ?? 'generating');
      setTaskMessage(task.message || null);
      updateStreamText(payload.stream_text);
      updateReportDraft(payload.report_draft);
      if (payload.debug_input) {
        setDebugInput(payload.debug_input);
      }
    },
    onTaskCompleted: (task) => {
      if (task.taskId !== activeTaskId) return;
      const payload = readTaskPayload(task.result);
      if (payload.report) {
        setReport(payload.report);
        setDebugInput(payload.report.debug_input);
      }
      updateStreamText(payload.stream_text);
      setReportDraft((current) => mergeTaskDraft(current, payload.report_draft ?? null));
      if (payload.debug_input) {
        setDebugInput(payload.debug_input);
      }
      setTaskError(null);
      setStreamPhase('completed');
      setTaskMessage(task.message || '模型研判生成完成');
      clearActiveTask();
      void loadLatest();
    },
    onTaskFailed: (task) => {
      if (task.taskId !== activeTaskId) return;
      setTaskError(task.error || task.message || '模型研判生成失败。');
      setStreamPhase('failed');
      setTaskMessage(task.message || task.error || '模型研判生成失败');
      clearActiveTask();
    },
  });

  const displayReport = useMemo(
    () => mergeDraftIntoReport(report, reportDraft, debugInput),
    [report, reportDraft, debugInput],
  );
  const renderReport = useMemo(
    () => (hasStructuredContent(displayReport) ? displayReport : null),
    [displayReport],
  );

  const displayText = useMemo(() => {
    const candidate = isGenerating
      ? streamText
      : (displayReport?.raw_stream_output || displayReport?.full_report || reportDraft?.raw_stream_output || reportDraft?.full_report || streamText);
    return String(candidate || '').trim();
  }, [displayReport?.full_report, displayReport?.raw_stream_output, isGenerating, reportDraft?.full_report, reportDraft?.raw_stream_output, streamText]);

  const streamKind = useMemo(() => detectStreamKind(displayText), [displayText]);
  const formattedDisplayText = useMemo(() => formatStreamText(displayText), [displayText]);
  const streamStatsText = useMemo(() => {
    if (!displayText) {
      return '等待首个分片';
    }
    return `${displayText.length.toLocaleString()} 字符`;
  }, [displayText]);

  return {
    report,
    loading,
    submitting,
    error,
    taskError,
    streamText,
    streamPhase,
    taskMessage,
    isGenerating,
    displayReport,
    renderReport: renderReport as MarketMainlineReportResponse | null,
    displayText,
    streamKind,
    formattedDisplayText,
    streamStatsText,
    debugInput,
    reportDraft,
    loadLatest,
    startGeneration,
  };
}
