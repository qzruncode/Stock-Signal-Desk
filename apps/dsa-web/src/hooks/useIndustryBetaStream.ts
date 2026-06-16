import { useState, useRef, useCallback, useEffect } from 'react';
import { useTaskStream } from './useTaskStream';
import { buyDecisionWorkbenchApi } from '../api/buyDecisionWorkbench';

export interface AnalysisChatMessage {
  id: string;
  type: 'data_collection' | 'llm_streaming' | 'result';
  timestamp: string;
  source?: string;
  message?: string;
  detail?: Record<string, unknown>;
  streamText?: string;
  result?: Record<string, unknown>;
}

export interface UseIndustryBetaStreamResult {
  taskId: string | null;
  messages: AnalysisChatMessage[];
  isRunning: boolean;
  isCompleted: boolean;
  startAnalysis: () => Promise<void>;
  error: string | null;
}

let msgIdCounter = 0;
function nextMsgId(): string {
  msgIdCounter += 1;
  return `msg-${msgIdCounter}-${Date.now()}`;
}

export function useIndustryBetaStream(options: {
  sessionId: string | null;
  symbol: string | null;
}): UseIndustryBetaStreamResult {
  const { sessionId, symbol } = options;

  const [taskId, setTaskId] = useState<string | null>(null);
  const [messages, setMessages] = useState<AnalysisChatMessage[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [isCompleted, setIsCompleted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const taskIdRef = useRef<string | null>(null);
  const lastLogCountRef = useRef(0);

  const processTask = useCallback((task: { taskId: string; status: string; progress: number; message?: string; result?: Record<string, unknown>; error?: string }) => {
    if (task.taskId !== taskIdRef.current) return;

    if (task.status === 'failed') {
      setIsRunning(false);
      setError(task.error || task.message || '分析失败');
      return;
    }

    if (task.status === 'completed') {
      setIsRunning(false);
      setIsCompleted(true);
      const taskResult = task.result as Record<string, unknown> | undefined;
      const parsedResult = taskResult?.result as Record<string, unknown> | undefined;
      if (parsedResult && Object.keys(parsedResult).length > 0) {
        setMessages((prev) => [
          ...prev,
          {
            id: nextMsgId(),
            type: 'result',
            timestamp: new Date().toISOString(),
            result: parsedResult,
          },
        ]);
      }
      return;
    }

    if (task.status === 'processing' || task.status === 'pending') {
      const result = task.result as Record<string, unknown> | undefined;
      const phase = result?.phase as string | undefined;

      if (phase === 'llm_streaming') {
        const streamText = (result?.stream_text as string) || '';
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.type === 'llm_streaming') {
            return [...prev.slice(0, -1), { ...last, streamText }];
          }
          return [
            ...prev,
            {
              id: nextMsgId(),
              type: 'llm_streaming',
              timestamp: new Date().toISOString(),
              streamText,
            },
          ];
        });
      }

      // Add data_collection messages from collection_logs
      const logs = result?.collection_logs as Array<{ source: string; message: string }> | undefined;
      if (logs && logs.length > 0) {
        setMessages((prev) => {
          const existingCount = prev.filter((m) => m.type === 'data_collection').length;
          if (logs.length <= existingCount) return prev;
          const newLogs = logs.slice(existingCount);
          return [
            ...prev,
            ...newLogs.map((log) => ({
              id: nextMsgId(),
              type: 'data_collection' as const,
              timestamp: new Date().toISOString(),
              source: log.source,
              message: log.message,
            })),
          ];
        });
      }
    }
  }, []);

  useTaskStream({
    onTaskProgress: processTask,
    onTaskCompleted: processTask,
    onTaskFailed: processTask,
    enabled: isRunning,
  });

  const startAnalysis = useCallback(async () => {
    if (!sessionId) return;
    setError(null);
    setIsRunning(true);
    setIsCompleted(false);
    setMessages([]);
    lastLogCountRef.current = 0;

    try {
      const { task_id } = await buyDecisionWorkbenchApi.runStepStreaming(sessionId);
      setTaskId(task_id);
      taskIdRef.current = task_id;
      setMessages((prev) => [
        ...prev,
        {
          id: nextMsgId(),
          type: 'data_collection',
          timestamp: new Date().toISOString(),
          source: '系统',
          message: `分析任务已创建: ${task_id}`,
        },
      ]);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '启动分析失败';
      setError(msg);
      setIsRunning(false);
    }
  }, [sessionId]);

  // Reset state when session/symbol changes
  useEffect(() => {
    setTaskId(null);
    setMessages([]);
    setIsRunning(false);
    setIsCompleted(false);
    setError(null);
    taskIdRef.current = null;
  }, [sessionId, symbol]);

  return {
    taskId,
    messages,
    isRunning,
    isCompleted,
    startAnalysis,
    error,
  };
}
