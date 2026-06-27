import type React from 'react';
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { AssistantRuntimeProvider, useThreadRuntime } from '@assistant-ui/react';
import { useDataStreamRuntime } from '@assistant-ui/react-data-stream';
import type { ExportedMessageRepository } from '@assistant-ui/core';
import { AlertTriangleIcon, PanelLeftCloseIcon, PanelLeftIcon, XIcon } from 'lucide-react';
import { agentApi, type ChatConversationDetail, type ChatConversationItem } from '../api/agent';

const Thread = lazy(() => import('../components/assistant-ui/thread'));
import { ThreadListSidebar } from '../components/assistant-ui/threadlist-sidebar';
import { useAssistantTools } from '../hooks/useAssistantTools';
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

  const runtime = useDataStreamRuntime({
    api: '/api/v1/agent/chat',
    protocol: 'data-stream',
    body: () => (
      selectedConversationId ? { conversation_id: selectedConversationId } : undefined
    ),
    onResponse: async (response) => {
      if (response.ok) {
        setStreamError(null);
        return;
      }
      const message = await response.clone().text().catch(() => '');
      throw new Error(message || `请求失败：HTTP ${response.status}`);
    },
    onError: (error) => {
      if (error instanceof TypeError && error.message?.includes('enqueue')) {
        return;
      }
      console.error('[Chat] Stream error:', error);
      setStreamError(error.message || '对话请求失败，请稍后重试');
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

  useAssistantTools();

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
