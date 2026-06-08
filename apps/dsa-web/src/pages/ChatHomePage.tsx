import type React from 'react';
import { useState } from 'react';
import { AssistantRuntimeProvider } from '@assistant-ui/react';
import { useDataStreamRuntime } from '@assistant-ui/react-data-stream';
import { AlertTriangleIcon, PanelLeftCloseIcon, PanelLeftIcon, XIcon } from 'lucide-react';
import { Thread } from '../components/assistant-ui/thread';
import { ThreadListSidebar } from '../components/assistant-ui/threadlist-sidebar';
import { useAssistantTools } from '../hooks/useAssistantTools';
import { cn } from '../utils/cn';

const ChatHomePage: React.FC = () => {
  const [streamError, setStreamError] = useState<string | null>(null);
  const runtime = useDataStreamRuntime({
    api: '/api/v1/agent/chat',
    protocol: 'data-stream',
    onResponse: async (response) => {
      if (response.ok) {
        setStreamError(null);
        return;
      }
      const message = await response
        .clone()
        .text()
        .catch(() => '');
      throw new Error(message || `请求失败：HTTP ${response.status}`);
    },
    onError: (error) => {
      // Suppress ReadableStream close-race errors that occur when the
      // stream is cancelled mid-flight (e.g. user navigates away or
      // starts a new conversation while the previous response is still
      // streaming).  This is a known issue in assistant-stream's
      // TextStreamControllerImpl which does not guard append() against
      // a closed ReadableStream.
      if (
        error instanceof TypeError &&
        error.message?.includes('enqueue')
      ) {
        return;
      }
      console.error('[Chat] Stream error:', error);
      setStreamError(error.message || '对话请求失败，请稍后重试');
    },
    onFinish: () => setStreamError(null),
  });

  // Register frontend tool renderers
  useAssistantTools();

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ChatLayout streamError={streamError} onDismissError={() => setStreamError(null)} />
    </AssistantRuntimeProvider>
  );
};

/* ── Layout: Sidebar + Thread ────────────────────────────────────────── */

const ChatLayout: React.FC<{
  streamError: string | null;
  onDismissError: () => void;
}> = ({ streamError, onDismissError }) => {
  const [sidebarOpen, setSidebarOpen] = useState(true);

  return (
    <div className="flex h-full bg-background">
      {/* Thread list sidebar */}
      {sidebarOpen && <ThreadListSidebar />}

      {/* Main chat area */}
      <div className="relative flex min-w-0 flex-1 flex-col">
        {/* Sidebar toggle */}
        <button
          type="button"
          onClick={() => setSidebarOpen((v) => !v)}
          className={cn(
            'absolute left-3 top-3 z-10 flex size-8 items-center justify-center',
            'rounded-lg border border-border bg-card text-muted-foreground',
            'shadow-sm transition hover:text-foreground',
          )}
          title={sidebarOpen ? '收起侧栏' : '展开侧栏'}
        >
          {sidebarOpen ? (
            <PanelLeftCloseIcon className="size-4" />
          ) : (
            <PanelLeftIcon className="size-4" />
          )}
        </button>

        {streamError && (
          <div className="absolute left-14 right-4 top-3 z-10 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 shadow-sm">
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="font-medium">AI 助手请求失败</p>
              <p className="mt-0.5 break-words text-xs opacity-90">{streamError}</p>
            </div>
            <button
              type="button"
              onClick={onDismissError}
              className="flex size-6 shrink-0 items-center justify-center rounded text-red-700 transition hover:bg-red-100"
              title="关闭"
            >
              <XIcon className="size-4" />
            </button>
          </div>
        )}

        <Thread />
      </div>
    </div>
  );
};

export default ChatHomePage;
