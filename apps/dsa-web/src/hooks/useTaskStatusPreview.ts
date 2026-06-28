import { useCallback, useMemo, useState } from 'react';
import { analysisApi } from '../api/analysis';
import type { AnalysisReport, TaskInfo, TaskStatus } from '../types/analysis';

export interface UseTaskStatusPreviewResult {
  selectedTaskStatus: TaskStatus | null;
  isLoadingTaskStatus: boolean;
  taskPreviewReport: AnalysisReport | null;
  setSelectedTaskStatus: React.Dispatch<React.SetStateAction<TaskStatus | null>>;
  selectTask: (
    task: TaskInfo,
    onSetPending: (stockCode: string) => void,
    onClearPending: () => void,
    scrollContainer?: HTMLElement | null,
  ) => void;
  clearSelectedTaskStatus: () => void;
  updateSelectedTaskStatusFromTask: (task: TaskInfo) => void;
}

export function useTaskStatusPreview(activeTasks: TaskInfo[]): UseTaskStatusPreviewResult {
  const [selectedTaskStatus, setSelectedTaskStatus] = useState<TaskStatus | null>(null);
  const [isLoadingTaskStatus, setIsLoadingTaskStatus] = useState(false);

  const taskPreviewReport = useMemo<AnalysisReport | null>(() => {
    if (!selectedTaskStatus) {
      return null;
    }

    if (selectedTaskStatus.result?.report) {
      return selectedTaskStatus.result.report;
    }

    const activeTask = activeTasks.find((task) => task.taskId === selectedTaskStatus.taskId);
    const conversation = activeTask?.conversation || selectedTaskStatus.conversation;
    if (!conversation) {
      return null;
    }

    const response = conversation.response || '';
    return {
      meta: {
        queryId: selectedTaskStatus.taskId,
        stockCode: activeTask?.stockCode || '',
        stockName: selectedTaskStatus.stockName || activeTask?.stockName || '',
        reportType: 'conversation',
        reportLanguage: 'zh',
        createdAt: new Date().toISOString(),
        modelUsed: conversation.modelUsed,
      },
      summary: {
        analysisSummary: response || 'AI 正在生成输出',
        operationAdvice: '',
        trendPrediction: '',
        sentimentScore: 50,
      },
      conversation,
    };
  }, [activeTasks, selectedTaskStatus]);

  const selectTask = useCallback(
    (
      task: TaskInfo,
      onSetPending: (stockCode: string) => void,
      onClearPending: () => void,
      scrollContainer?: HTMLElement | null,
    ) => {
      onSetPending(task.stockCode);
      setSelectedTaskStatus({
        taskId: task.taskId,
        status: task.status,
        progress: task.progress,
        error: task.error,
        stockName: task.stockName,
        originalQuery: task.originalQuery,
        selectionSource: task.selectionSource,
        promptTemplateId: task.promptTemplateId,
        promptTemplateName: task.promptTemplateName,
        conversation: task.conversation,
      });
      setIsLoadingTaskStatus(true);
      void analysisApi.getStatus(task.taskId)
        .then((status) => {
          setSelectedTaskStatus(status);
          if (status.result?.report || status.conversation?.response) {
            onClearPending();
          }
        })
        .catch((err) => {
          onClearPending();
          console.warn('加载任务对话失败:', err);
        })
        .finally(() => {
          setIsLoadingTaskStatus(false);
        });
      scrollContainer?.scrollTo({ top: 0, behavior: 'smooth' });
    },
    [],
  );

  const clearSelectedTaskStatus = useCallback(() => {
    setSelectedTaskStatus(null);
  }, []);

  const updateSelectedTaskStatusFromTask = useCallback((task: TaskInfo) => {
    setSelectedTaskStatus((current) => (
      current?.taskId === task.taskId
        ? {
            taskId: task.taskId,
            status: task.status,
            progress: task.progress,
            error: task.error,
            stockName: task.stockName,
            originalQuery: task.originalQuery,
            selectionSource: task.selectionSource,
            promptTemplateId: task.promptTemplateId,
            promptTemplateName: task.promptTemplateName,
            conversation: task.conversation,
          }
        : current
    ));
  }, []);

  return {
    selectedTaskStatus,
    isLoadingTaskStatus,
    taskPreviewReport,
    setSelectedTaskStatus,
    selectTask,
    clearSelectedTaskStatus,
    updateSelectedTaskStatusFromTask,
  };
}

export default useTaskStatusPreview;
