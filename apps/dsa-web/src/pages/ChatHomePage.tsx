import type React from 'react';
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import {
  AssistantRuntimeProvider,
  useThreadRuntime,
  CompositeAttachmentAdapter,
  SimpleTextAttachmentAdapter,
} from '@assistant-ui/react';
import { useDataStreamRuntime } from '@assistant-ui/react-data-stream';
import type { ExportedMessageRepository } from '@assistant-ui/core';
import { AlertTriangleIcon, PanelLeftCloseIcon, PanelLeftIcon, XIcon } from 'lucide-react';
import { agentApi, type ChatConversationDetail, type ChatConversationItem } from '../api/agent';
import { extractErrorPayloadText } from '../api/error';
import { ApprovalContext, type PendingApproval } from '../components/assistant-ui/tool-ui/ApprovalContext';

const Thread = lazy(() => import('../components/assistant-ui/thread'));
import { ThreadListSidebar } from '../components/assistant-ui/threadlist-sidebar';
import { cn } from '../utils/cn';

const normalizeMessageRole = (role: string): 'user' | 'assistant' | 'system' => {
  if (role === 'user' || role === 'assistant' || role === 'system') {
    return role;
  }
  return 'assistant';
};

const toRuntimeMessages = (messages: ChatConversationDetail['messages']) =>
  messages
    .filter((message) => (message.content || '').trim().length > 0)
    .map((message) => ({
      id: message.id,
      role: normalizeMessageRole(message.role),
      createdAt: message.createdAt ? new Date(message.createdAt) : new Date(),
      content: [{ type: 'text' as const, text: message.content || '' }],
    }));

async function readStreamErrorMessage(response: Response): Promise<string> {
  const rawText = await response.clone().text().catch(() => '');
  if (!rawText.trim()) {
    return `请求失败：HTTP ${response.status}`;
  }

  try {
    const payload = JSON.parse(rawText) as unknown;
    return extractErrorPayloadText(payload) || rawText;
  } catch {
    return rawText;
  }
}

const ChatRuntimeBridge: React.FC<{
  conversationDetail: ChatConversationDetail | null;
  onThreadRuntime: (threadRuntime: ReturnType<typeof useThreadRuntime>) => void;
}> = ({ conversationDetail, onThreadRuntime }) => {
  const threadRuntime = useThreadRuntime();
  const conversationId = conversationDetail?.id ?? null;

  useEffect(() => {
    onThreadRuntime(threadRuntime);
  }, [onThreadRuntime, threadRuntime]);

  useEffect(() => {
    threadRuntime.cancelRun();
    threadRuntime.reset([]);
    if (conversationDetail?.threadState?.messages?.length) {
      threadRuntime.import(conversationDetail.threadState as unknown as ExportedMessageRepository);
      return;
    }
    threadRuntime.reset(conversationDetail ? toRuntimeMessages(conversationDetail.messages) : []);
  }, [conversationDetail, conversationId, threadRuntime]);

  return null;
};

const ChatHomePage: React.FC = () => {
  const [conversations, setConversations] = useState<ChatConversationItem[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [selectedConversationDetail, setSelectedConversationDetail] = useState<ChatConversationDetail | null>(null);
  const [isLoadingConversations, setIsLoadingConversations] = useState(true);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState<Record<string, PendingApproval>>({});
  const threadRuntimeRef = useRef<ReturnType<typeof useThreadRuntime> | null>(null);

  const refreshConversations = useCallback(async () => {
    const response = await agentApi.listConversations();
    setConversations(response.items);
    return response.items;
  }, []);

  const loadConversationDetail = useCallback(async (conversationId: string) => {
    const detail = await agentApi.getConversation(conversationId);
    setSelectedConversationDetail(detail);
    return detail;
  }, []);

  const createConversation = useCallback(async () => {
    const created = await agentApi.createConversation();
    await refreshConversations();
    setSelectedConversationId(created.id);
    setSelectedConversationDetail({ ...created, messages: [] });
    return created;
  }, [refreshConversations]);

  const ensureInitialConversation = useCallback(async () => {
    setIsLoadingConversations(true);
    try {
      const items = await refreshConversations();
      if (items.length === 0) {
        await createConversation();
        return;
      }
      setSelectedConversationId((current) => current || items[0]?.id || null);
    } finally {
      setIsLoadingConversations(false);
    }
  }, [createConversation, refreshConversations]);

  useEffect(() => {
    void ensureInitialConversation();
  }, [ensureInitialConversation]);

  useEffect(() => {
    if (!selectedConversationId) {
      return;
    }
    void loadConversationDetail(selectedConversationId);
  }, [loadConversationDetail, selectedConversationId]);

  const handleApproveToolCall = useCallback((toolCallId: string, approved: boolean) => {
    void (async () => {
      try {
        await agentApi.approveToolCall(toolCallId, approved);
      } catch (err) {
        console.error('[Chat] approveToolCall failed:', err);
      } finally {
        // 无论成功失败,从 pending 列表移除(后端会超时自行处理)
        setPendingApprovals((prev) => {
          if (!prev[toolCallId]) return prev;
          const next = { ...prev };
          delete next[toolCallId];
          return next;
        });
      }
    })();
  }, []);

  const runtime = useDataStreamRuntime({
    api: '/api/v1/agent/chat',
    protocol: 'data-stream',
    adapters: {
      // 文本类附件(CSV/JSON/MD/TXT)端到端打通:SimpleTextAttachmentAdapter
      // 在 send() 时把文件内容包成 <attachment> text part,后端 _join_text_parts
      // 已认 text part,无需后端改动。
      attachments: new CompositeAttachmentAdapter([new SimpleTextAttachmentAdapter()]),
    },
    body: () => (
      selectedConversationId ? { conversation_id: selectedConversationId } : undefined
    ),
    onData: (event) => {
      // 后端 controller.add_data({"type":"approval-request",...}) 经 2: data chunk 到达
      const payload = event.data as Record<string, unknown> | undefined;
      if (payload && payload.type === 'approval-request' && typeof payload.tool_call_id === 'string') {
        const approval: PendingApproval = {
          tool_call_id: payload.tool_call_id,
          tool_name: String(payload.tool_name ?? ''),
          symbol: String(payload.symbol ?? ''),
          reason: String(payload.reason ?? ''),
        };
        setPendingApprovals((prev) => ({ ...prev, [approval.tool_call_id]: approval }));
      }
    },
    onResponse: async (response) => {
      if (response.ok) {
        setStreamError(null);
        return;
      }
      throw new Error(await readStreamErrorMessage(response));
    },
    onError: (error) => {
      // 用户主动取消(点 Stop):AbortError / DOMException.AbortError 不算错误
      if (
        (error instanceof TypeError && error.message?.includes('enqueue')) ||
        error.name === 'AbortError' ||
        (error instanceof DOMException && error.name === 'AbortError')
      ) {
        return;
      }
      console.error('[Chat] Stream error:', error);
      setStreamError(error.message || '对话请求失败，请稍后重试');
    },
    onCancel: () => {
      // 取消由 onError 的 AbortError 过滤兜底,这里仅确保不残留错误态
      setStreamError(null);
    },
    onFinish: async () => {
      setStreamError(null);
      if (!selectedConversationId) {
        return;
      }
      const exportedThread = threadRuntimeRef.current?.export();
      if (exportedThread) {
        const detail = await agentApi.syncConversationSnapshot(selectedConversationId, {
          threadState: exportedThread,
        });
        setSelectedConversationDetail(detail);
      }
      await refreshConversations();
    },
  });

  const handleCreateConversation = useCallback(() => {
    void createConversation();
  }, [createConversation]);

  const handleSelectConversation = useCallback((conversationId: string) => {
    setSelectedConversationDetail(null);
    setSelectedConversationId(conversationId);
  }, []);

  const handleRenameConversation = useCallback((conversation: ChatConversationItem) => {
    const nextTitle = window.prompt('输入新的对话名称', conversation.title || '新对话');
    if (!nextTitle || nextTitle.trim() === conversation.title) {
      return;
    }
    void (async () => {
      await agentApi.renameConversation(conversation.id, nextTitle.trim());
      await refreshConversations();
      if (conversation.id === selectedConversationId) {
        await loadConversationDetail(conversation.id);
      }
    })();
  }, [loadConversationDetail, refreshConversations, selectedConversationId]);

  const handleDeleteConversation = useCallback((conversation: ChatConversationItem) => {
    const confirmed = window.confirm(`确认删除对话“${conversation.title || '新对话'}”吗？`);
    if (!confirmed) {
      return;
    }
    void (async () => {
      await agentApi.deleteConversation(conversation.id);
      const items = await refreshConversations();
      if (conversation.id !== selectedConversationId) {
        return;
      }
      setSelectedConversationDetail(null);
      if (items.length === 0) {
        await createConversation();
        return;
      }
      setSelectedConversationId(items[0]?.id || null);
    })();
  }, [createConversation, refreshConversations, selectedConversationId]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ApprovalContext.Provider value={{ pendingApprovals, approveToolCall: handleApproveToolCall }}>
      <ChatRuntimeBridge
        conversationDetail={selectedConversationDetail}
        onThreadRuntime={(threadRuntime) => {
          threadRuntimeRef.current = threadRuntime;
        }}
      />
      <ChatLayout
        streamError={streamError}
        onDismissError={() => setStreamError(null)}
        conversations={conversations}
        selectedConversationId={selectedConversationId}
        isLoadingConversations={isLoadingConversations}
        onCreateConversation={handleCreateConversation}
        onSelectConversation={handleSelectConversation}
        onRenameConversation={handleRenameConversation}
        onDeleteConversation={handleDeleteConversation}
        onBatchDeleteConversations={async (conversationIds) => {
          const titles = conversations
            .filter((conversation) => conversationIds.includes(conversation.id))
            .map((conversation) => conversation.title || '新对话');
          const confirmed = window.confirm(
            `确认批量删除 ${conversationIds.length} 个对话吗？\n${titles.slice(0, 5).join('\n')}${titles.length > 5 ? '\n...' : ''}`,
          );
          if (!confirmed) {
            return;
          }

          await Promise.all(conversationIds.map((conversationId) => agentApi.deleteConversation(conversationId)));
          const items = await refreshConversations();
          if (selectedConversationId && conversationIds.includes(selectedConversationId)) {
            setSelectedConversationDetail(null);
            if (items.length === 0) {
              await createConversation();
              return;
            }
            setSelectedConversationId(items[0]?.id || null);
          }
        }}
      />
      </ApprovalContext.Provider>
    </AssistantRuntimeProvider>
  );
};

const ChatLayout: React.FC<{
  streamError: string | null;
  onDismissError: () => void;
  conversations: ChatConversationItem[];
  selectedConversationId: string | null;
  isLoadingConversations: boolean;
  onCreateConversation: () => void;
  onSelectConversation: (conversationId: string) => void;
  onRenameConversation: (conversation: ChatConversationItem) => void;
  onDeleteConversation: (conversation: ChatConversationItem) => void;
  onBatchDeleteConversations: (conversationIds: string[]) => Promise<void>;
}> = ({
  streamError,
  onDismissError,
  conversations,
  selectedConversationId,
  isLoadingConversations,
  onCreateConversation,
  onSelectConversation,
  onRenameConversation,
  onDeleteConversation,
  onBatchDeleteConversations,
}) => {
  const [isDesktop, setIsDesktop] = useState(false);
  const [mobileSidebarState, setMobileSidebarState] = useState<'closed' | 'open' | 'closing'>('closed');

  useEffect(() => {
    if (typeof window === 'undefined') {
      return undefined;
    }

    const mediaQuery = window.matchMedia('(min-width: 1024px)');
    const syncLayout = (matches: boolean) => {
      setIsDesktop(matches);
      if (matches) {
        setMobileSidebarState('closed');
      }
    };

    syncLayout(mediaQuery.matches);

    const handleChange = (event: MediaQueryListEvent) => {
      syncLayout(event.matches);
    };

    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, []);

  useEffect(() => {
    if (mobileSidebarState !== 'closing') {
      return undefined;
    }

    const timeout = window.setTimeout(() => setMobileSidebarState('closed'), 180);
    return () => window.clearTimeout(timeout);
  }, [mobileSidebarState]);

  const mobileSidebarOpen = mobileSidebarState !== 'closed';

  return (
    <div className="flex h-full min-h-0 overflow-hidden bg-background">
      {isDesktop ? (
        <ThreadListSidebar
          side="left"
          className="w-64"
          conversations={conversations}
          selectedConversationId={selectedConversationId}
          isLoading={isLoadingConversations}
          onCreate={onCreateConversation}
          onSelect={onSelectConversation}
          onRename={onRenameConversation}
          onDelete={onDeleteConversation}
          onBatchDelete={onBatchDeleteConversations}
        />
      ) : null}

      {!isDesktop && mobileSidebarOpen ? (
        <div className="fixed inset-0 z-30 lg:hidden" onClick={() => setMobileSidebarState('closing')}>
          <div
            className={cn(
              'page-drawer-overlay absolute inset-0 backdrop-blur-[2px]',
              mobileSidebarState === 'closing' ? 'mobile-drawer-overlay-out' : 'mobile-drawer-overlay',
            )}
          />
          <div
            className={cn(
              'absolute inset-y-0 right-0 w-[min(19rem,84vw)] shadow-2xl',
              mobileSidebarState === 'closing' ? 'mobile-drawer-right-out' : 'mobile-drawer-right',
            )}
            onClick={(event) => event.stopPropagation()}
          >
            <ThreadListSidebar
              side="right"
              conversations={conversations}
              selectedConversationId={selectedConversationId}
              isLoading={isLoadingConversations}
              onCreate={onCreateConversation}
              onSelect={onSelectConversation}
              onRename={onRenameConversation}
              onDelete={onDeleteConversation}
              onBatchDelete={onBatchDeleteConversations}
            />
          </div>
        </div>
      ) : null}

      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <button
          type="button"
          onClick={() => {
            if (isDesktop) {
              return;
            }
            setMobileSidebarState((value) => (value === 'open' ? 'closing' : 'open'));
          }}
          className={cn(
            'fixed right-3 top-3 z-20 flex h-9 w-9 items-center justify-center rounded-xl border border-border',
            'bg-card text-muted-foreground',
            'shadow-sm transition hover:text-foreground',
            'sm:h-10 sm:w-10',
            'lg:absolute lg:right-3 lg:top-3 lg:h-9 lg:w-9 lg:rounded-lg lg:border lg:translate-y-0',
          )}
          aria-label={mobileSidebarOpen ? '收起对话列表' : '展开对话列表'}
        >
          {mobileSidebarOpen ? <PanelLeftCloseIcon className="size-4" /> : <PanelLeftIcon className="size-4" />}
        </button>

        {streamError ? (
          <div className="absolute left-3 right-13 top-14 z-10 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 shadow-sm sm:left-4 sm:right-14 sm:top-3 lg:left-4">
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="font-medium">AI 助手请求失败</p>
              <p className="mt-0.5 break-words text-xs opacity-90">{streamError}</p>
            </div>
            <button
              type="button"
              onClick={onDismissError}
              className="flex size-6 shrink-0 items-center justify-center rounded text-red-700 transition hover:bg-red-100"
              aria-label="关闭"
            >
              <XIcon className="size-4" />
            </button>
          </div>
        ) : null}

        <div className="min-h-0 flex-1">
          <Suspense fallback={<ChatLoadingFallback />}>
            <Thread />
          </Suspense>
        </div>
      </div>
    </div>
  );
};

function ChatLoadingFallback() {
  return (
    <div className="flex h-full min-h-0 items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        <p className="text-sm text-muted-foreground">正在加载 AI 投研助手...</p>
      </div>
    </div>
  );
}

export default ChatHomePage;
