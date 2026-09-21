import { startTransition, useCallback, useEffect, useRef, useState } from 'react';
import {
  useThreadRuntime,
  CompositeAttachmentAdapter,
  SimpleTextAttachmentAdapter,
  WebSpeechDictationAdapter,
  WebSpeechSynthesisAdapter,
} from '@assistant-ui/react';
import { useDataStreamRuntime } from '@assistant-ui/react-data-stream';
import { agentApi, type ChatConversationDetail, type ChatConversationItem } from '../api/agent';
import { toApiErrorMessage } from '../api/error';
import {
  removeUserTurnFromThread,
  repositoryToConversationSnapshotMessages,
} from '../utils/chatMessageDeletion';
import {
  isStreamAbortError,
  readStreamErrorMessage,
  readThrownStreamErrorMessage,
} from '../utils/chatStreamError';
import { currentUserRequest } from '../utils/agentRequestTransport';
import {
  persistAgentMode,
  readStoredAgentMode,
  type AgentProductMode,
} from '../utils/agentMode';
import {
  TERMINAL_RUN_STATUSES,
  type ActiveStream,
  type ConversationLoadState,
} from '../utils/chatHomeConstants';

import { useQueryClient } from '@tanstack/react-query';
import { conversationKey } from '../utils/conversationQueries';
import { useConversationActions } from './useConversationActions';

export function useChatController() {
  const [conversations, setConversations] = useState<ChatConversationItem[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [selectedConversationDetail, setSelectedConversationDetail] = useState<ChatConversationDetail | null>(null);
  const [conversationLoadState, setConversationLoadState] = useState<ConversationLoadState>(null);
  const [conversationLoadAttempt, setConversationLoadAttempt] = useState(0);
  const [isLoadingConversations, setIsLoadingConversations] = useState(true);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [approvalDecision, setApprovalDecision] = useState<'approve' | 'reject' | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [agentMode, setAgentMode] = useState<AgentProductMode>(() => readStoredAgentMode());
  const threadRuntimeRef = useRef<ReturnType<typeof useThreadRuntime> | null>(null);
  const resumeExistingRef = useRef<{
    conversationId: string;
    afterChunkIndex: number;
  } | null>(null);
  const selectedConversationIdRef = useRef<string | null>(null);
  const activeStreamRef = useRef<ActiveStream | null>(null);
  const queryClient = useQueryClient();

  useEffect(() => {
    persistAgentMode(agentMode);
  }, [agentMode]);

  useEffect(() => {
    selectedConversationIdRef.current = selectedConversationId;
    setApprovalDecision(null);
    setApprovalError(null);
  }, [selectedConversationId]);

  useEffect(() => {
    setApprovalDecision(null);
    setApprovalError(null);
  }, [selectedConversationDetail?.pendingInterrupt?.interruptId]);

  const refreshConversations = useCallback(async () => {
    const response = await queryClient.fetchQuery({ queryKey: ['conversations'], queryFn: () => agentApi.listConversations(), staleTime: 0 });
    setConversations(response.items);
    return response.items;
  }, [queryClient]);

  const rememberConversationDetail = useCallback((detail: ChatConversationDetail) => {
    if (detail.isGenerating) queryClient.removeQueries({ queryKey: conversationKey(detail.id), exact: true });
    else queryClient.setQueryData(conversationKey(detail.id), detail);
  }, [queryClient]);

  const loadConversationDetail = useCallback(async (conversationId: string) => {
    const detail = await queryClient.fetchQuery({ queryKey: conversationKey(conversationId), queryFn: ({ signal }) => agentApi.getConversation(conversationId, signal), staleTime: 0 });
    rememberConversationDetail(detail);
    // A slower previous request must never overwrite the conversation the
    // user has selected in the meantime.
    if (selectedConversationIdRef.current === conversationId) {
      startTransition(() => {
        setSelectedConversationDetail(detail);
      });
    }
    return detail;
  }, [queryClient, rememberConversationDetail]);

  const beginConversationSelection = useCallback((conversationId: string) => {
    resumeExistingRef.current = null;
    if (selectedConversationIdRef.current === conversationId) {
      return false;
    }

    const cachedDetail = queryClient.getQueryData<ChatConversationDetail>(conversationKey(conversationId)) ?? null;
    selectedConversationIdRef.current = conversationId;
    setSelectedConversationId(conversationId);
    setConversationLoadState(cachedDetail ? null : {
      conversationId,
      status: 'loading',
    });
    setStreamError(null);

    if (cachedDetail) {
      // Keep the sidebar selection urgent. Restoring a large message tree can
      // happen in a transition without delaying the click feedback.
      startTransition(() => {
        setSelectedConversationDetail(cachedDetail);
      });
    }
    return true;
  }, [queryClient]);

  const createConversation = useCallback(async () => {
    const created = await agentApi.createConversation();
    await refreshConversations();
    selectedConversationIdRef.current = created.id;
    setSelectedConversationId(created.id);
    const detail = { ...created, messages: [] };
    rememberConversationDetail(detail);
    setSelectedConversationDetail(detail);
    setConversationLoadState(null);
    return created;
  }, [refreshConversations, rememberConversationDetail]);

  const ensureInitialConversation = useCallback(async () => {
    setIsLoadingConversations(true);
    try {
      const items = await refreshConversations();
      if (items.length === 0) {
        await createConversation();
        return;
      }
      if (!selectedConversationIdRef.current && items[0]?.id) {
        beginConversationSelection(items[0].id);
      }
    } catch (error) {
      setStreamError(toApiErrorMessage(error, '会话列表加载失败，请检查服务后重试'));
    } finally {
      setIsLoadingConversations(false);
    }
  }, [beginConversationSelection, createConversation, refreshConversations]);

  useEffect(() => {
    void ensureInitialConversation();
  }, [ensureInitialConversation]);

  useEffect(() => {
    if (!selectedConversationId) {
      return;
    }
    void loadConversationDetail(selectedConversationId)
      .then(() => {
        if (selectedConversationIdRef.current === selectedConversationId) {
          setConversationLoadState(null);
        }
      })
      .catch((error) => {
        if (selectedConversationIdRef.current !== selectedConversationId) {
          return;
        }
        setConversationLoadState({
          conversationId: selectedConversationId,
          status: 'error',
          message: toApiErrorMessage(error, '会话内容加载失败，请稍后重试'),
        });
      });
  }, [conversationLoadAttempt, loadConversationDetail, selectedConversationId]);

  const prepareResumeExisting = useCallback((conversationId: string, afterChunkIndex: number | null) => {
    if (afterChunkIndex == null) {
      if (!conversationId || resumeExistingRef.current?.conversationId === conversationId) {
        resumeExistingRef.current = null;
      }
      return;
    }
    resumeExistingRef.current = { conversationId, afterChunkIndex };
  }, []);

  const handleUserCancelRun = useCallback(() => {
    const conversationId = selectedConversationIdRef.current;
    if (!conversationId) return;
    void agentApi.cancelConversationRun(conversationId)
      .then(() => loadConversationDetail(conversationId))
      .catch((error) => {
        setStreamError(toApiErrorMessage(error, '停止生成失败，请稍后重试'));
      });
  }, [loadConversationDetail]);

  const handleInterruptDecision = useCallback((decision: 'approve' | 'reject') => {
    const conversationId = selectedConversationIdRef.current;
    const pending = selectedConversationDetail?.pendingInterrupt;
    if (!conversationId || !pending || approvalDecision !== null) return;
    setApprovalDecision(decision);
    setApprovalError(null);
    void (async () => {
      try {
        await agentApi.decideInterrupt(conversationId, pending.interruptId, {
          runId: pending.runId,
          fingerprint: pending.fingerprint,
          decision,
        });
        for (let attempt = 0; attempt < 12; attempt += 1) {
          const detail = await loadConversationDetail(conversationId);
          if (
            detail.pendingInterrupt?.interruptId !== pending.interruptId
            || detail.isGenerating
            || detail.resumeState?.status !== 'interrupted'
          ) {
            break;
          }
          await new Promise((resolve) => window.setTimeout(resolve, 100));
        }
        await refreshConversations();
      } catch (error) {
        setApprovalError(toApiErrorMessage(error, '审批已过期或处理失败，请刷新后重试'));
        void loadConversationDetail(conversationId).catch(() => undefined);
      } finally {
        setApprovalDecision(null);
      }
    })();
  }, [approvalDecision, loadConversationDetail, refreshConversations, selectedConversationDetail?.pendingInterrupt]);

  const handleDeleteUserTurn = useCallback((messageId: string) => {
    const conversationId = selectedConversationIdRef.current;
    const threadRuntime = threadRuntimeRef.current;
    if (!conversationId || !threadRuntime) return;

    const exportedThread = threadRuntime.export();
    const nextThread = removeUserTurnFromThread(exportedThread, messageId);
    if (!nextThread) return;

    activeStreamRef.current = null;
    resumeExistingRef.current = null;
    threadRuntime.cancelRun();
    threadRuntime.import(nextThread);
    void threadRuntime.composer.reset();

    void (async () => {
      try {
        await agentApi.cancelConversationRun(conversationId).catch(() => false);
        const detail = await agentApi.syncConversationSnapshot(conversationId, {
          messages: repositoryToConversationSnapshotMessages(nextThread),
          pruneAgentContextToMessages: true,
        });
        if (selectedConversationIdRef.current === conversationId) {
          setSelectedConversationDetail(detail);
        }
        await refreshConversations();
      } catch (error) {
        setStreamError(toApiErrorMessage(error, '删除对话记录失败，请刷新后重试'));
        void loadConversationDetail(conversationId).catch(() => undefined);
      }
    })();
  }, [loadConversationDetail, refreshConversations]);

  // 同一对话已有活跃 run 时,/agent/chat 返回 409。改为重新拉取详情触发续流,
  // 而非报错(详情带 isGenerating=true → ChatRuntimeBridge 走续流分支)。
  const handleRunInProgress = useCallback(() => {
    if (!selectedConversationId) return;
    void loadConversationDetail(selectedConversationId).catch((error) => {
      setStreamError(toApiErrorMessage(error, '恢复生成状态失败，请稍后重试'));
    });
  }, [loadConversationDetail, selectedConversationId]);

  const reconcileConversationAfterStream = useCallback(async (conversationId: string) => {
    const detail = await loadConversationDetail(conversationId);
    await refreshConversations();
    return detail;
  }, [loadConversationDetail, refreshConversations]);

  const shouldDetachTerminalStream = useCallback((conversationId: string, runId: string | null | undefined) => {
    const activeStream = activeStreamRef.current;
    if (!activeStream || activeStream.conversationId !== conversationId) {
      return false;
    }
    // When a user starts a fresh turn from an already terminal conversation,
    // the detail endpoint still reports the preceding run until the new run
    // has been admitted.  That old terminal snapshot must never cancel the
    // fresh browser request before it reaches the server.
    if (activeStream.startedFromTerminalSnapshot) {
      return Boolean(runId && runId !== activeStream.initialRunId);
    }
    return true;
  }, []);

  const runtime = useDataStreamRuntime({
    api: '/api/v1/agent/chat',
    protocol: 'data-stream',
    adapters: {
      // 文本类附件(CSV/JSON/MD/TXT)端到端打通:SimpleTextAttachmentAdapter
      // 在 send() 时把文件内容包成 <attachment> text part,后端 _join_text_parts
      // 已认 text part,无需后端改动。
      attachments: new CompositeAttachmentAdapter([new SimpleTextAttachmentAdapter()]),
      // 朗读回答(TTS):浏览器原生 speechSynthesis,零后端。未配置时 ActionBar Speak 自动隐藏。
      speech: new WebSpeechSynthesisAdapter(),
      // 语音输入(麦克风):浏览器原生 SpeechRecognition,零后端。zh-CN 适配中文提问。
      // 不支持的浏览器下 Composer Dictate 按钮自动不渲染(useComposerDictate 返回 null)。
      dictation: new WebSpeechDictationAdapter({ language: 'zh-CN', interimResults: true }),
    },
    body: () => {
      if (!selectedConversationId) return undefined;
      const initialResumeState = selectedConversationDetail?.id === selectedConversationId
        ? selectedConversationDetail.resumeState
        : undefined;
      const initialRunId = initialResumeState?.runId ?? null;
      const startedFromTerminalSnapshot = TERMINAL_RUN_STATUSES.has(
        initialResumeState?.status ?? '',
      );
      const resumeExisting = resumeExistingRef.current;
      if (resumeExisting?.conversationId === selectedConversationId) {
        activeStreamRef.current = {
          conversationId: selectedConversationId,
          resumeExisting: true,
          initialRunId,
          startedFromTerminalSnapshot,
        };
        return {
          conversation_id: selectedConversationId,
          resume_existing: true,
          after_chunk_index: resumeExisting.afterChunkIndex,
          // Preserve assistant-stream text/tool parts so reconnects rebuild the
          // same ordered plan -> tool -> next-turn presentation.
          stream_presentation: 'timeline',
        };
      }
      activeStreamRef.current = {
        conversationId: selectedConversationId,
        resumeExisting: false,
        initialRunId,
        startedFromTerminalSnapshot,
      };
      queryClient.removeQueries({ queryKey: conversationKey(selectedConversationId), exact: true });
      const request = currentUserRequest(threadRuntimeRef.current?.export());
      if (!request) {
        return {
          conversation_id: selectedConversationId,
          agent_mode: agentMode,
        };
      }
      return {
        conversation_id: selectedConversationId,
        messages: request.messages,
        history_mode: 'server',
        history_parent_id: request.historyParentId,
        agent_mode: agentMode,
        stream_presentation: 'timeline',
      };
    },
    onResponse: async (response) => {
      if (response.ok) {
        setStreamError(null);
        resumeExistingRef.current = null;
        return;
      }
      // 409:同一对话已有活跃 run → 触发续流,不报错。
      if (response.status === 409) {
        activeStreamRef.current = null;
        handleRunInProgress();
        return;
      }
      // 注意:不能在此 throw。useDataStreamRuntime 的 onResponse 在 try 块之外
      // 调用(useDataStreamRuntime.js),throw 不会进 onError,而是变成 fetch 的
      // 未捕获 rejection 被静默吞掉,红条永远不出现。直接 setStreamError 即可。
      activeStreamRef.current = null;
      const message = await readStreamErrorMessage(response);
      setStreamError(message);
    },
    onError: (error) => {
      const failedStream = activeStreamRef.current;
      activeStreamRef.current = null;
      // Only an explicit AbortError represents a local cancellation. A broken
      // ReadableStream can also mention enqueue and must be reconciled.
      if (isStreamAbortError(error)) {
        return;
      }
      // 409 已在 onResponse 处理(触发续流),此处静默,不弹红条。
      if (error.message?.includes('Status 409')) {
        return;
      }
      console.error('[Chat] Stream error:', error);
      const fallbackMessage = readThrownStreamErrorMessage(error);
      if (!failedStream) {
        setStreamError(fallbackMessage);
        return;
      }

      // A server restart can leave assistant-ui's local ReadableStream marked
      // as running after its transport has already disappeared.  Release that
      // stale local run before hydrating the durable conversation state; the
      // bridge will then attach to the persisted run (or render its terminal
      // result) instead of leaving the timeline frozen at its last stage.
      threadRuntimeRef.current?.cancelRun();
      setStreamError('连接中断，正在恢复已生成的回答...');
      void reconcileConversationAfterStream(failedStream.conversationId)
        .then((detail) => {
          const hasAssistantAnswer = detail.messages.some(
            (message) => message.role === 'assistant' && message.content.trim().length > 0,
          );
          setStreamError(
            detail.isGenerating || hasAssistantAnswer
              ? null
              : fallbackMessage,
          );
        })
        .catch(() => {
          setStreamError(fallbackMessage);
        });
    },
    onCancel: () => {
      // Runtime hydration and conversation switching also cancel the local
      // stream.  They must not kill the detached backend run; only the visible
      // Stop button calls handleUserCancelRun explicitly.
      activeStreamRef.current = null;
      setStreamError(null);
    },
    onFinish: async () => {
      const finishedStream = activeStreamRef.current;
      activeStreamRef.current = null;
      try {
        if (!finishedStream) {
          await refreshConversations();
          return;
        }
        const conversationId = finishedStream.conversationId;
        if (selectedConversationIdRef.current !== conversationId) {
          await refreshConversations();
          return;
        }
        // The terminal LangGraph transaction has already persisted canonical
        // messages and the bounded durable execution trace.  Do not write the
        // assistant-ui export back here: it is an opaque live rendering
        // snapshot and may contain repeated, large tool payloads.
        await reconcileConversationAfterStream(conversationId);
      } catch (error) {
        setStreamError(toApiErrorMessage(error, '回答已生成，但会话同步失败，请刷新后重试'));
      }
    },
  });

  const handleCreateConversation = useCallback(() => {
    resumeExistingRef.current = null;
    void createConversation().catch((error) => {
      setStreamError(toApiErrorMessage(error, '新建会话失败，请稍后重试'));
    });
  }, [createConversation]);

  const handleSelectConversation = useCallback((conversationId: string) => {
    beginConversationSelection(conversationId);
  }, [beginConversationSelection]);

  const handleRetryConversation = useCallback(() => {
    const conversationId = selectedConversationIdRef.current;
    if (!conversationId) {
      return;
    }
    setConversationLoadState({
      conversationId,
      status: 'loading',
    });
    setConversationLoadAttempt((current) => current + 1);
  }, []);

  const actions = useConversationActions({
    conversations, selectedConversationId, selectedConversationIdRef, setSelectedConversationId,
    setSelectedConversationDetail, setConversationLoadState, setStreamError, activeStreamRef,
    resumeExistingRef, threadRuntimeRef, beginConversationSelection, createConversation,
    loadConversationDetail, refreshConversations,
  });
  const isConversationSwitching = Boolean(
    selectedConversationId
    && selectedConversationDetail?.id !== selectedConversationId,
  );
  const conversationSwitchError = (
    isConversationSwitching
    && conversationLoadState?.conversationId === selectedConversationId
    && conversationLoadState.status === 'error'
  )
    ? conversationLoadState.message || '会话内容加载失败，请稍后重试'
    : null;
  return { ...actions,
    runtime, selectedConversationDetail, prepareResumeExisting, shouldDetachTerminalStream,
    threadRuntimeRef, streamError, setStreamError, conversations,
    selectedConversationId, isLoadingConversations, isConversationSwitching, conversationSwitchError,
    handleRetryConversation, handleCreateConversation, handleSelectConversation, handleDeleteUserTurn,
    handleUserCancelRun, approvalDecision, approvalError, handleInterruptDecision,
    agentMode, setAgentMode,
  };
}
