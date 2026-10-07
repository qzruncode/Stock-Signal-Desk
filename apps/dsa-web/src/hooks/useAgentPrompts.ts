import { useCallback, useEffect, useMemo, useState } from 'react';
import { agentPromptsApi } from '../api/agentPrompts';
import type {
  ActiveAgentPromptResponse,
  AgentPromptTemplate,
  CreateAgentPromptRequest,
  UpdateAgentPromptRequest,
} from '../types/agentPrompts';

type AgentPromptsStatus = 'loading' | 'ready' | 'error';

export interface UseAgentPromptsResult {
  status: AgentPromptsStatus;
  templates: AgentPromptTemplate[];
  activePrompt: ActiveAgentPromptResponse | null;
  error: string | null;
  refetch: () => void;
  createPrompt: (payload: CreateAgentPromptRequest) => Promise<void>;
  updatePrompt: (templateId: number, payload: UpdateAgentPromptRequest) => Promise<void>;
  deletePrompt: (templateId: number) => Promise<void>;
  activatePrompt: (templateId: number) => Promise<void>;
  /** 当前是否有 mutation 进行中（保存/删除/切换）。 */
  mutating: boolean;
}

/**
 * 管理 AI 助手 system prompt 模板的列表加载与 CRUD。
 */
export function useAgentPrompts(): UseAgentPromptsResult {
  const [templates, setTemplates] = useState<AgentPromptTemplate[]>([]);
  const [activePrompt, setActivePrompt] = useState<ActiveAgentPromptResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingKey, setPendingKey] = useState(0);
  const [mutating, setMutating] = useState(false);

  const status: AgentPromptsStatus = useMemo(() => {
    if (error !== null) return 'error';
    if (pendingKey === 0 && templates.length === 0 && activePrompt === null) return 'loading';
    return 'ready';
  }, [error, pendingKey, templates, activePrompt]);

  useEffect(() => {
    let active = true;
    Promise.all([agentPromptsApi.listPrompts(), agentPromptsApi.getActivePrompt()])
      .then(([list, activeResp]) => {
        if (!active) return;
        setTemplates(list);
        setActivePrompt(activeResp);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : '加载 prompt 模板失败');
        setTemplates([]);
        setActivePrompt(null);
      });
    return () => {
      active = false;
    };
  }, [pendingKey]);

  const refetch = useCallback(() => {
    setPendingKey((value) => value + 1);
  }, []);

  const runMutation = useCallback(
    async (fn: () => Promise<unknown>) => {
      setMutating(true);
      try {
        await fn();
        // 重新拉取列表 + 生效状态
        const [list, activeResp] = await Promise.all([
          agentPromptsApi.listPrompts(),
          agentPromptsApi.getActivePrompt(),
        ]);
        setTemplates(list);
        setActivePrompt(activeResp);
        setError(null);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : '操作失败');
        throw err;
      } finally {
        setMutating(false);
      }
    },
    [],
  );

  const createPrompt = useCallback(
    (payload: CreateAgentPromptRequest) => runMutation(() => agentPromptsApi.createPrompt(payload)),
    [runMutation],
  );

  const updatePrompt = useCallback(
    (templateId: number, payload: UpdateAgentPromptRequest) =>
      runMutation(() => agentPromptsApi.updatePrompt(templateId, payload)),
    [runMutation],
  );

  const deletePrompt = useCallback(
    (templateId: number) => runMutation(() => agentPromptsApi.deletePrompt(templateId)),
    [runMutation],
  );

  const activatePrompt = useCallback(
    (templateId: number) => runMutation(() => agentPromptsApi.activatePrompt(templateId)),
    [runMutation],
  );

  return {
    status,
    templates,
    activePrompt,
    error,
    refetch,
    createPrompt,
    updatePrompt,
    deletePrompt,
    activatePrompt,
    mutating,
  };
}
