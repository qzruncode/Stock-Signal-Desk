import type React from 'react';
import { lazy, Suspense, useEffect, useState } from 'react';
import {
  AlertTriangleIcon,
  HistoryIcon,
  Loader2Icon,
  PanelLeftCloseIcon,
  PanelLeftIcon,
  RefreshCcwIcon,
  ShieldAlertIcon,
  XIcon,
} from 'lucide-react';
import type {
  ChatConversationItem,
  PendingAgentInterrupt,
} from '../../api/agent';
import { cn } from '../../utils/cn';
import { CheckpointHistoryDrawer } from './CheckpointHistoryDrawer';
import { QuestionNavigator } from './QuestionNavigator';
import { ThreadListSidebar } from './threadlist-sidebar';

const Thread = lazy(() => import('./thread'));

export type ChatLayoutProps = {
  streamError: string | null;
  onDismissError: () => void;
  conversations: ChatConversationItem[];
  selectedConversationId: string | null;
  isLoadingConversations: boolean;
  isConversationSwitching: boolean;
  conversationSwitchError: string | null;
  onRetryConversation: () => void;
  onCreateConversation: () => void;
  onSelectConversation: (conversationId: string) => void;
  onRenameConversation: (conversation: ChatConversationItem) => void;
  onDeleteConversation: (conversation: ChatConversationItem) => void;
  onBatchDeleteConversations: (conversationIds: string[]) => void;
  onDeleteUserTurn: (messageId: string) => void;
  onCancelRun: () => void;
  isClearingConversations: boolean;
  onClearAllConversations: () => void;
  pendingInterrupt: PendingAgentInterrupt | null;
  approvalDecision: 'approve' | 'reject' | null;
  approvalError: string | null;
  onInterruptDecision: (decision: 'approve' | 'reject') => void;
};

export const ChatLayout: React.FC<ChatLayoutProps> = ({
  streamError,
  onDismissError,
  conversations,
  selectedConversationId,
  isLoadingConversations,
  isConversationSwitching,
  conversationSwitchError,
  onRetryConversation,
  onCreateConversation,
  onSelectConversation,
  onRenameConversation,
  onDeleteConversation,
  onBatchDeleteConversations,
  onDeleteUserTurn,
  onCancelRun,
  isClearingConversations,
  onClearAllConversations,
  pendingInterrupt,
  approvalDecision,
  approvalError,
  onInterruptDecision,
}) => {
  const [isDesktop, setIsDesktop] = useState(false);
  const [isDesktopSidebarCollapsed, setIsDesktopSidebarCollapsed] = useState(false);
  const [mobileSidebarState, setMobileSidebarState] = useState<'closed' | 'open' | 'closing'>('closed');
  const [isCheckpointHistoryOpen, setIsCheckpointHistoryOpen] = useState(false);

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

  useEffect(() => {
    setIsCheckpointHistoryOpen(false);
  }, [selectedConversationId]);

  const mobileSidebarOpen = mobileSidebarState !== 'closed';

  return (
    <div className="flex h-full min-h-0 overflow-hidden bg-background">
      {isDesktop ? (
        <div
          className={cn(
            'relative hidden h-full shrink-0 transition-[width] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] lg:flex',
            isDesktopSidebarCollapsed
              ? 'w-0 overflow-visible border-r border-transparent bg-transparent'
              : 'w-64 overflow-hidden border-r border-border/80 bg-white',
          )}
        >
          <div
            className={cn(
              'h-full w-64 shrink-0 transition-[opacity,transform] duration-200 ease-[cubic-bezier(0.22,1,0.36,1)]',
              isDesktopSidebarCollapsed ? 'pointer-events-none -translate-x-2 opacity-0' : 'translate-x-0 opacity-100 delay-75',
            )}
          >
            <ThreadListSidebar
              side="left"
              className="border-r-0"
              conversations={conversations}
              selectedConversationId={selectedConversationId}
              isLoading={isLoadingConversations}
              loadingConversationId={isConversationSwitching ? selectedConversationId : null}
              onCreate={onCreateConversation}
              onSelect={onSelectConversation}
              onRename={onRenameConversation}
              onDelete={onDeleteConversation}
              onBatchDelete={onBatchDeleteConversations}
              isClearingAll={isClearingConversations}
              onClearAll={onClearAllConversations}
              onCollapse={() => setIsDesktopSidebarCollapsed(true)}
            />
          </div>
          <div
            className={cn(
              'absolute left-2 top-2 z-20 flex transition-[opacity,transform] duration-200 ease-[cubic-bezier(0.22,1,0.36,1)]',
              isDesktopSidebarCollapsed ? 'translate-x-0 opacity-100 delay-150' : 'pointer-events-none -translate-x-1 opacity-0',
            )}
          >
            <button
              type="button"
              onClick={() => setIsDesktopSidebarCollapsed(false)}
              className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition hover:bg-muted hover:text-foreground"
              aria-label="展开对话列表"
              title="展开对话列表"
            >
              <PanelLeftIcon className="size-3.5" />
            </button>
          </div>
        </div>
      ) : null}

      {!isDesktop && mobileSidebarOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden" onClick={() => setMobileSidebarState('closing')}>
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
              loadingConversationId={isConversationSwitching ? selectedConversationId : null}
              onCreate={() => {
                setMobileSidebarState('closing');
                onCreateConversation();
              }}
              onSelect={(conversationId) => {
                // Start the drawer transition immediately; loading the detail
                // continues behind the composited closing animation.
                setMobileSidebarState('closing');
                onSelectConversation(conversationId);
              }}
              onRename={onRenameConversation}
              onDelete={onDeleteConversation}
              onBatchDelete={onBatchDeleteConversations}
              isClearingAll={isClearingConversations}
              onClearAll={() => {
                setMobileSidebarState('closing');
                onClearAllConversations();
              }}
            />
          </div>
        </div>
      ) : null}

      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <div className="relative z-30 flex shrink-0 items-center justify-end gap-3 px-3 pb-2 pt-1 lg:px-6 lg:py-1">
          <div className="flex items-center gap-3">
            {selectedConversationId ? (
              <button
                type="button"
                onClick={() => setIsCheckpointHistoryOpen(true)}
                className={cn(
                  'flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border',
                  'bg-card text-muted-foreground shadow-sm transition hover:border-primary/30 hover:text-foreground',
                  'sm:h-8 sm:w-8',
                  isCheckpointHistoryOpen && 'border-primary/30 text-primary shadow-md',
                )}
                aria-label="查看 Checkpoint 历史"
                aria-expanded={isCheckpointHistoryOpen}
                title="Checkpoint 历史"
              >
                <HistoryIcon className="size-3.5" />
              </button>
            ) : null}
            <QuestionNavigator />
            {!isDesktop ? (
              <button
                type="button"
                onClick={() => setMobileSidebarState((value) => (value === 'open' ? 'closing' : 'open'))}
                className={cn(
                  'flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border',
                  'bg-card text-muted-foreground shadow-sm transition hover:text-foreground',
                  'sm:h-8 sm:w-8',
                )}
                aria-label={mobileSidebarOpen ? '收起对话列表' : '展开对话列表'}
              >
                {mobileSidebarOpen ? <PanelLeftCloseIcon className="size-3" /> : <PanelLeftIcon className="size-3" />}
              </button>
            ) : null}
          </div>
        </div>

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

        <div className="relative min-h-0 flex-1">
          <Suspense fallback={<ChatLoadingFallback />}>
            <Thread onUserCancel={onCancelRun} onDeleteUserTurn={onDeleteUserTurn} />
          </Suspense>
          {pendingInterrupt ? (
            <ApprovalCard
              interrupt={pendingInterrupt}
              decision={approvalDecision}
              error={approvalError}
              onDecision={onInterruptDecision}
            />
          ) : null}
          {isConversationSwitching ? (
            <ConversationSwitchOverlay
              error={conversationSwitchError}
              onRetry={onRetryConversation}
            />
          ) : null}
        </div>
      </div>
      <CheckpointHistoryDrawer
        isOpen={isCheckpointHistoryOpen}
        conversationId={selectedConversationId}
        onClose={() => setIsCheckpointHistoryOpen(false)}
      />
    </div>
  );
};

function ApprovalCard({
  interrupt,
  decision,
  error,
  onDecision,
}: {
  interrupt: PendingAgentInterrupt;
  decision: 'approve' | 'reject' | null;
  error: string | null;
  onDecision: (decision: 'approve' | 'reject') => void;
}) {
  const busy = decision !== null;
  return (
    <div className="absolute bottom-24 left-3 right-3 z-20 mx-auto max-w-2xl rounded-2xl border border-amber-300/70 bg-amber-50/95 p-4 shadow-xl backdrop-blur sm:left-5 sm:right-5">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-xl bg-amber-100 text-amber-700">
          <ShieldAlertIcon className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-amber-950">需要你的确认</p>
          <p className="mt-1 text-sm leading-6 text-amber-900">{interrupt.summary}</p>
          <div className="mt-2 rounded-lg border border-amber-200/80 bg-white/70 px-3 py-2 text-xs text-amber-950">
            <p><span className="text-amber-700">操作：</span>{interrupt.toolName}</p>
            {Object.keys(interrupt.arguments || {}).length > 0 ? (
              <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] leading-5 text-amber-900">
                {JSON.stringify(interrupt.arguments, null, 2)}
              </pre>
            ) : null}
          </div>
          {error ? <p className="mt-2 text-xs text-red-700">{error}</p> : null}
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => onDecision('approve')}
              className="inline-flex h-9 items-center justify-center rounded-lg bg-amber-700 px-4 text-sm font-medium text-white transition hover:bg-amber-800 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {decision === 'approve' ? <Loader2Icon className="mr-2 size-4 animate-spin" /> : null}
              批准一次
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onDecision('reject')}
              className="inline-flex h-9 items-center justify-center rounded-lg border border-amber-300 bg-white px-4 text-sm font-medium text-amber-900 transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {decision === 'reject' ? <Loader2Icon className="mr-2 size-4 animate-spin" /> : null}
              拒绝
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

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

function ConversationSwitchOverlay({
  error,
  onRetry,
}: {
  error: string | null;
  onRetry: () => void;
}) {
  return (
    <div
      className="absolute inset-0 z-10 flex min-h-0 flex-col bg-background animate-in fade-in duration-200"
      aria-busy={error ? undefined : true}
      aria-live="polite"
    >
      <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-6 pb-24">
        <div className="mx-auto flex max-w-sm flex-col items-center text-center">
          {error ? (
            <>
              <div className="flex size-10 items-center justify-center rounded-2xl bg-amber-50 text-amber-600">
                <AlertTriangleIcon className="size-5" />
              </div>
              <p className="mt-4 text-sm font-medium text-foreground">会话暂时没加载出来</p>
              <p className="mt-1.5 text-xs leading-5 text-muted-foreground">{error}</p>
              <button
                type="button"
                onClick={onRetry}
                className="mt-4 inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-card px-3 text-xs font-medium text-foreground shadow-sm transition hover:bg-muted"
              >
                <RefreshCcwIcon className="size-3.5" />
                重新加载
              </button>
            </>
          ) : (
            <>
              <div className="flex size-10 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-600">
                <Loader2Icon className="size-5 animate-spin motion-reduce:animate-none" />
              </div>
              <p className="mt-4 text-sm font-medium text-foreground">正在切换会话</p>
              <p className="mt-1.5 text-xs text-muted-foreground">已响应，正在同步会话内容...</p>
              <div className="mt-6 w-full space-y-2.5" aria-hidden="true">
                <div className="h-2.5 w-3/4 animate-pulse rounded-full bg-muted motion-reduce:animate-none" />
                <div className="h-2.5 w-full animate-pulse rounded-full bg-muted motion-reduce:animate-none" />
                <div className="h-2.5 w-5/6 animate-pulse rounded-full bg-muted motion-reduce:animate-none" />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
