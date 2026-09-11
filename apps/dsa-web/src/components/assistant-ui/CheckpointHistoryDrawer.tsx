import type { FC } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  GitBranchIcon,
  HistoryIcon,
  Loader2Icon,
  RefreshCcwIcon,
} from 'lucide-react';
import {
  agentApi,
  type AgentCheckpointHistoryResponse,
  type AgentCheckpointSummary,
} from '../../api/agent';
import { toApiErrorMessage } from '../../api/error';
import { Drawer } from '../common';
import { cn } from '../../utils/cn';

const CHECKPOINT_PAGE_SIZE = 20;

const STATUS_LABELS: Record<string, string> = {
  running: '执行中',
  interrupted: '等待审批',
  completed: '已完成',
  partial: '部分完成',
  failed: '失败',
  cancelled: '已取消',
};

const statusLabel = (status?: string | null) => (
  STATUS_LABELS[status || ''] || status || '未知状态'
);

const statusClassName = (status?: string | null) => {
  switch (status) {
    case 'running':
      return 'border-sky-200 bg-sky-50 text-sky-700';
    case 'interrupted':
      return 'border-amber-200 bg-amber-50 text-amber-700';
    case 'completed':
      return 'border-emerald-200 bg-emerald-50 text-emerald-700';
    case 'partial':
      return 'border-orange-200 bg-orange-50 text-orange-700';
    case 'failed':
    case 'cancelled':
      return 'border-red-200 bg-red-50 text-red-700';
    default:
      return 'border-border bg-muted text-muted-foreground';
  }
};

const shortenId = (value?: string | null) => {
  if (!value) return '—';
  if (value.length <= 22) return value;
  return `${value.slice(0, 10)}…${value.slice(-8)}`;
};

const formatCheckpointTime = (value?: string | null) => {
  if (!value) return '时间未知';
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(timestamp);
};

const safeItems = (history: AgentCheckpointHistoryResponse | null) => history?.items || [];

export interface CheckpointHistoryDrawerProps {
  isOpen: boolean;
  conversationId: string | null;
  onClose: () => void;
}

/** Read-only view of LangGraph revisions; it never resumes or rewinds a run. */
export const CheckpointHistoryDrawer: FC<CheckpointHistoryDrawerProps> = ({
  isOpen,
  conversationId,
  onClose,
}) => {
  const [history, setHistory] = useState<AgentCheckpointHistoryResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSequence = useRef(0);

  const loadHistory = useCallback(async (beforeCheckpointId: string | null = null, append = false) => {
    if (!conversationId) return;

    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;
    if (append) {
      setIsLoadingMore(true);
    } else {
      setIsLoading(true);
      setError(null);
    }

    try {
      const response = await agentApi.getConversationCheckpoints(conversationId, {
        limit: CHECKPOINT_PAGE_SIZE,
        beforeCheckpointId,
      });
      if (requestSequence.current !== requestId) return;
      setHistory((current) => (
        append && current
          ? { ...response, items: [...current.items, ...response.items] }
          : response
      ));
    } catch (requestError) {
      if (requestSequence.current !== requestId) return;
      setError(toApiErrorMessage(requestError, 'Checkpoint 历史加载失败，请稍后重试'));
    } finally {
      if (requestSequence.current === requestId) {
        setIsLoading(false);
        setIsLoadingMore(false);
      }
    }
  }, [conversationId]);

  useEffect(() => {
    requestSequence.current += 1;
    if (!isOpen) return;

    setHistory(null);
    setError(null);
    void loadHistory();
  }, [isOpen, conversationId, loadHistory]);

  const items = safeItems(history);
  const currentCheckpointId = history?.currentCheckpointId || null;

  return (
    <Drawer
      isOpen={isOpen}
      onClose={onClose}
      title="Checkpoint 历史"
      eyebrow="LANGGRAPH CHECKPOINT"
      width="max-w-xl"
      backdropClassName="bg-slate-950/25"
    >
      <div id="checkpoint-history-drawer-content" className="space-y-4">
        <div className="rounded-xl border border-primary/15 bg-primary/[0.045] p-3.5">
          <div className="flex items-start gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <HistoryIcon className="size-4.5" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-semibold text-foreground">图状态版本</p>
                <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
                  <CheckCircle2Icon className="size-3" />
                  只读
                </span>
              </div>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                这里查看 LangGraph saver 的图内状态快照；运行生命周期仍由 agent_run 负责。此处不会回放工具，也不会改变当前会话。
              </p>
            </div>
            <button
              type="button"
              onClick={() => void loadHistory()}
              disabled={isLoading || isLoadingMore || !conversationId}
              className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg border border-border/70 bg-card text-muted-foreground transition hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
              aria-label="刷新 checkpoint 历史"
              title="刷新"
            >
              <RefreshCcwIcon className={cn('size-3.5', isLoading && 'animate-spin')} />
            </button>
          </div>
          {history ? (
            <div className="mt-3 grid grid-cols-2 gap-2 border-t border-primary/10 pt-3 text-[10px]">
              <div className="min-w-0">
                <span className="text-muted-foreground">当前 checkpoint</span>
                <code className="mt-1 block truncate font-mono text-foreground" title={currentCheckpointId || undefined}>
                  {shortenId(currentCheckpointId)}
                </code>
              </div>
              <div className="min-w-0">
                <span className="text-muted-foreground">图线程</span>
                <code className="mt-1 block truncate font-mono text-foreground" title={history.threadId}>
                  {shortenId(history.threadId)}
                </code>
              </div>
            </div>
          ) : null}
        </div>

        {error ? (
          <div className="rounded-xl border border-red-200 bg-red-50/80 p-3 text-xs text-red-800" role="alert">
            <div className="flex items-start gap-2">
              <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="font-medium">无法读取 checkpoint 历史</p>
                <p className="mt-1 break-words leading-5 opacity-90">{error}</p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => void loadHistory()}
              className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-lg border border-red-200 bg-white px-2.5 text-xs font-medium text-red-700 transition hover:bg-red-100"
            >
              <RefreshCcwIcon className="size-3.5" />
              重试
            </button>
          </div>
        ) : null}

        {isLoading && !history ? (
          <div className="flex min-h-40 items-center justify-center rounded-xl border border-dashed border-border px-4 text-xs text-muted-foreground">
            <Loader2Icon className="mr-2 size-4 animate-spin text-primary" />
            正在读取图状态历史…
          </div>
        ) : null}

        {!isLoading && !error && history && items.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border px-4 py-10 text-center">
            <GitBranchIcon className="mx-auto size-6 text-muted-foreground/60" />
            <p className="mt-3 text-sm font-medium text-foreground">当前会话还没有可查看的 checkpoint</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">开始一次对话后，LangGraph 的图状态版本会出现在这里。</p>
          </div>
        ) : null}

        <div className="space-y-3">
          {items.map((item, index) => (
            <CheckpointCard
              key={item.checkpointId || `${item.createdAt || 'checkpoint'}-${index}`}
              item={item}
              index={index}
              isCurrent={item.checkpointId === currentCheckpointId}
            />
          ))}
        </div>

        {history?.hasMore && history.nextBeforeCheckpointId ? (
          <button
            type="button"
            onClick={() => void loadHistory(history.nextBeforeCheckpointId || null, true)}
            disabled={isLoading || isLoadingMore}
            className="flex h-9 w-full items-center justify-center gap-1.5 rounded-lg border border-border bg-card text-xs font-medium text-foreground transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isLoadingMore ? <Loader2Icon className="size-3.5 animate-spin" /> : <ChevronDownIcon className="size-3.5" />}
            {isLoadingMore ? '正在加载…' : '加载更早的 checkpoint'}
          </button>
        ) : null}
      </div>
    </Drawer>
  );
};

const CheckpointCard: FC<{
  item: AgentCheckpointSummary;
  index: number;
  isCurrent: boolean;
}> = ({ item, index, isCurrent }) => {
  const state = item.state;
  const writeKeys = item.metadata?.writeKeys || [];
  const nextNodes = item.next || [];
  const tasks = item.tasks || [];

  return (
    <article className={cn(
      'rounded-xl border bg-card p-3.5 shadow-sm',
      isCurrent ? 'border-primary/35 ring-1 ring-primary/10' : 'border-border/80',
    )}>
      <div className="flex items-start gap-3">
        <div className={cn(
          'mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold tabular-nums',
          isCurrent ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground',
        )}>
          {index + 1}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-semibold text-foreground">Checkpoint {index + 1}</p>
            {isCurrent ? (
              <span className="rounded-full border border-primary/20 bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">当前</span>
            ) : null}
            <span className={cn('rounded-full border px-2 py-0.5 text-[10px] font-medium', statusClassName(state?.status))}>
              {statusLabel(state?.status)}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-muted-foreground">
            <span>{formatCheckpointTime(item.createdAt)}</span>
            {item.metadata?.step !== null && item.metadata?.step !== undefined ? <span>· step {item.metadata.step}</span> : null}
            {item.metadata?.source ? <span>· {item.metadata.source}</span> : null}
          </div>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        <Metric label="消息" value={state?.messageCount ?? 0} />
        <Metric label="工具结果" value={state?.toolResultCount ?? 0} />
        <Metric label="证据" value={state?.evidenceCount ?? 0} />
        <Metric label="模型轮次" value={state?.modelTurnCount ?? 0} />
      </div>

      <div className="mt-3 space-y-2 border-t border-border/60 pt-3 text-[10px]">
        <div className="flex items-start gap-2">
          <span className="w-14 shrink-0 text-muted-foreground">版本</span>
          <code className="min-w-0 break-all font-mono text-foreground" title={item.checkpointId || undefined}>
            {shortenId(item.checkpointId)}
          </code>
        </div>
        <div className="flex items-start gap-2">
          <span className="w-14 shrink-0 text-muted-foreground">父版本</span>
          <code className="min-w-0 break-all font-mono text-foreground" title={item.parentCheckpointId || undefined}>
            {shortenId(item.parentCheckpointId)}
          </code>
        </div>
        <div className="flex items-start gap-2">
          <span className="w-14 shrink-0 text-muted-foreground">下一节点</span>
          <span className="min-w-0 break-words text-foreground">
            {nextNodes.length > 0 ? nextNodes.join(' · ') : '图已暂停或结束'}
          </span>
        </div>
        <div className="flex items-start gap-2">
          <span className="w-14 shrink-0 text-muted-foreground">写入字段</span>
          <span className="min-w-0 break-words font-mono text-foreground">
            {writeKeys.length > 0 ? writeKeys.join(', ') : '无业务字段写入'}
          </span>
        </div>
      </div>

      {tasks.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1.5 border-t border-border/60 pt-3">
          {tasks.slice(0, 6).map((task, taskIndex) => (
            <span
              key={task.id || `${task.name || 'task'}-${taskIndex}`}
              className={cn(
                'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[10px]',
                task.hasError
                  ? 'border-red-200 bg-red-50 text-red-700'
                  : task.interruptCount > 0
                    ? 'border-amber-200 bg-amber-50 text-amber-700'
                    : 'border-border bg-muted/50 text-muted-foreground',
              )}
            >
              {task.name || task.id || '图任务'}
              {task.hasError ? ' · 失败' : task.interruptCount > 0 ? ` · 待审批 ${task.interruptCount}` : ''}
            </span>
          ))}
          {tasks.length > 6 ? <span className="px-1 py-1 text-[10px] text-muted-foreground">+{tasks.length - 6} 个任务</span> : null}
        </div>
      ) : null}
    </article>
  );
};

const Metric: FC<{ label: string; value: number }> = ({ label, value }) => (
  <div className="rounded-lg bg-muted/45 px-2 py-1.5">
    <p className="text-[10px] text-muted-foreground">{label}</p>
    <p className="mt-0.5 text-sm font-semibold tabular-nums text-foreground">{value}</p>
  </div>
);

export default CheckpointHistoryDrawer;
