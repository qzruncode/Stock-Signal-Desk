import type React from 'react';
import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import {
  ActivityIcon,
  AlertTriangleIcon,
  CheckCircle2Icon,
  Loader2Icon,
  PanelLeftCloseIcon,
  PanelLeftIcon,
  XIcon,
} from 'lucide-react';
import type { ChatConversationItem } from '../../api/agent';
import { analysisApi } from '../../api/analysis';
import { useTaskStream } from '../../hooks/useTaskStream';
import type { TaskInfo } from '../../types/analysis';
import { isFloatingAnalysisTaskVisible } from '../../utils/analysisTaskVisibility';
import { cn } from '../../utils/cn';
import { ThreadListSidebar } from './threadlist-sidebar';

const Thread = lazy(() => import('./thread'));

export type ChatLayoutProps = {
  streamError: string | null;
  onDismissError: () => void;
  conversations: ChatConversationItem[];
  selectedConversationId: string | null;
  isLoadingConversations: boolean;
  onCreateConversation: () => void;
  onSelectConversation: (conversationId: string) => void;
  onRenameConversation: (conversation: ChatConversationItem) => void;
  onDeleteConversation: (conversation: ChatConversationItem) => void;
  onDeleteUserTurn: (messageId: string) => void;
  onCancelRun: () => void;
  onBatchDeleteConversations: (conversationIds: string[]) => Promise<void>;
};

export const ChatLayout: React.FC<ChatLayoutProps> = ({
  streamError,
  onDismissError,
  conversations,
  selectedConversationId,
  isLoadingConversations,
  onCreateConversation,
  onSelectConversation,
  onRenameConversation,
  onDeleteConversation,
  onDeleteUserTurn,
  onCancelRun,
  onBatchDeleteConversations,
}) => {
  const [isDesktop, setIsDesktop] = useState(false);
  const [isDesktopSidebarCollapsed, setIsDesktopSidebarCollapsed] = useState(false);
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
              onCreate={onCreateConversation}
              onSelect={onSelectConversation}
              onRename={onRenameConversation}
              onDelete={onDeleteConversation}
              onBatchDelete={onBatchDeleteConversations}
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
        <AnalysisTaskActivity />
        <button
          type="button"
          onClick={() => {
            if (!isDesktop) {
              setMobileSidebarState((value) => (value === 'open' ? 'closing' : 'open'));
            }
          }}
          className={cn(
            'fixed right-3 top-3 z-20 flex h-9 w-9 items-center justify-center rounded-xl border border-border',
            'bg-card text-muted-foreground shadow-sm transition hover:text-foreground',
            'sm:h-10 sm:w-10 lg:hidden',
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
            <Thread onUserCancel={onCancelRun} onDeleteUserTurn={onDeleteUserTurn} />
          </Suspense>
        </div>
      </div>
    </div>
  );
};

function AnalysisTaskActivity() {
  const [tasks, setTasks] = useState<Record<string, TaskInfo>>({});
  const [open, setOpen] = useState(false);
  const upsert = useCallback((task: TaskInfo) => {
    setTasks((current) => ({ ...current, [task.taskId]: task }));
    if (task.status === 'completed' || task.status === 'failed') setOpen(true);
  }, []);

  useEffect(() => {
    analysisApi.getTasks({ limit: 20 })
      .then((response) => setTasks(Object.fromEntries(response.tasks.map((task) => [task.taskId, task]))))
      .catch(() => undefined);
  }, []);
  const { isConnected } = useTaskStream({
    onTaskCreated: upsert,
    onTaskStarted: upsert,
    onTaskProgress: upsert,
    onTaskCompleted: upsert,
    onTaskFailed: upsert,
  });
  const visible = Object.values(tasks)
    .filter(isFloatingAnalysisTaskVisible)
    .sort((left, right) => String(right.createdAt || '').localeCompare(String(left.createdAt || '')))
    .slice(0, 5);
  const activeCount = visible.filter((task) => task.status === 'pending' || task.status === 'processing').length;
  if (visible.length === 0) return null;

  return (
    <div className="absolute bottom-24 right-3 z-20 sm:right-5">
      {open && (
        <div className="mb-2 w-[min(22rem,calc(100vw-1.5rem))] rounded-xl border border-border bg-card/95 p-2.5 shadow-xl backdrop-blur">
          <div className="mb-2 flex items-center justify-between px-1 text-xs font-semibold text-foreground">
            <span>分析任务</span>
            <span className="text-[10px] text-muted-foreground">{isConnected ? '实时更新' : '正在重连'}</span>
          </div>
          <div className="space-y-1.5">
            {visible.map((task) => {
              const running = task.status === 'pending' || task.status === 'processing';
              return (
                <div key={task.taskId} className="rounded-lg bg-muted/45 px-2.5 py-2 text-[10px]">
                  <div className="flex items-center gap-2">
                    {running ? <Loader2Icon className="size-3.5 animate-spin text-primary" /> : <CheckCircle2Icon className={`size-3.5 ${task.status === 'failed' ? 'text-red-500' : 'text-emerald-500'}`} />}
                    <span className="font-medium text-foreground">{task.stockName || task.stockCode}</span>
                    <span className="ml-auto text-muted-foreground">{task.progress ?? (running ? 0 : 100)}%</span>
                  </div>
                  <p className="mt-1 line-clamp-1 text-muted-foreground">{task.error || task.message || task.status}</p>
                </div>
              );
            })}
          </div>
          <p className="mt-2 px-1 text-[10px] text-muted-foreground">任务完成后可直接问助手“读取刚完成的报告”。</p>
        </div>
      )}
      <button type="button" onClick={() => setOpen((value) => !value)} className="ml-auto flex h-10 items-center gap-2 rounded-full border border-border bg-card px-3 text-xs font-medium text-foreground shadow-lg transition hover:border-primary/30" aria-label="分析任务状态">
        <ActivityIcon className="size-4 text-primary" />
        {activeCount > 0 ? `${activeCount} 个分析中` : '分析任务'}
      </button>
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
