import type { FC } from 'react';
import { useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  AuiIf,
  ThreadPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  BranchPickerPrimitive,
  ActionBarPrimitive,
  useMessage,
  useThread,
  type TextMessagePartProps,
} from '@assistant-ui/react';
import {
  ArrowUpIcon,
  BotIcon,
  UserIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ChevronDownIcon,
  BrainCircuitIcon,
  CheckCircle2Icon,
  Loader2Icon,
  SparklesIcon,
  SquareIcon,
  CopyIcon,
  RefreshCwIcon,
  PencilIcon,
} from 'lucide-react';
import {
  GenericToolUI,
  KlineToolUI,
  RealtimeQuotesToolUI,
  FinancialsToolUI,
  NewsToolUI,
  BuyCriteriaToolUI,
} from '../../hooks/useAssistantTools';
import {
  ComposerAttachments,
  ComposerAddAttachment,
  ComposerAttachmentDropzone,
  UserMessageAttachments,
} from './attachment';
import { cn } from '../../utils/cn';

/* ── Thread (root) ───────────────────────────────────────────────────── */

const Thread: FC = () => {
  return (
    <ThreadPrimitive.Root className="flex h-full min-h-0 flex-col overflow-hidden">
      <ThreadPrimitive.Viewport className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto bg-[radial-gradient(circle_at_50%_0%,hsl(var(--primary)/0.08),transparent_34%),linear-gradient(180deg,hsl(var(--background)),hsl(var(--background)))] px-3 pt-16 pb-5 sm:gap-6 sm:px-4 sm:pt-6 sm:pb-6 lg:px-6">
        <AuiIf condition={(s) => s.thread.isEmpty}>
          <EmptyState />
        </AuiIf>

        <ThreadPrimitive.Messages
          components={{
            UserMessage,
            AssistantMessage,
          }}
        />

        <AuiIf condition={(s) => !s.thread.isEmpty}>
          <ThreadPrimitive.ViewportFooter className="sticky bottom-0 pt-2">
            <div className="mx-auto w-full max-w-3xl">
              <ThreadPrimitive.ScrollToBottom
                className={cn(
                  'mx-auto mb-2 flex size-8 items-center justify-center',
                  'rounded-full border border-border bg-card text-muted-foreground',
                  'shadow-sm transition hover:text-foreground',
                )}
              >
                <ChevronDownIcon className="size-4" />
              </ThreadPrimitive.ScrollToBottom>
            </div>
          </ThreadPrimitive.ViewportFooter>
        </AuiIf>
      </ThreadPrimitive.Viewport>

      <Composer />
    </ThreadPrimitive.Root>
  );
};

/* ── Empty State ─────────────────────────────────────────────────────── */

const EmptyState: FC = () => (
  <div className="flex flex-col items-center gap-5 px-2 pt-16 text-center sm:pt-20">
    <div className="flex size-16 items-center justify-center rounded-2xl border border-primary/20 bg-card text-primary shadow-[0_18px_50px_hsl(var(--primary)/0.14)]">
      <SparklesIcon className="size-7" />
    </div>
    <div>
      <h2 className="text-xl font-semibold text-foreground">AI 投研助手</h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
        输入股票、指数或市场问题，助手会自动规划数据、调用工具并生成投研回答。
      </p>
    </div>
    <div className="mt-2 flex flex-wrap justify-center gap-2">
      {SUGGESTIONS.map((suggestion) => (
        <ThreadPrimitive.Suggestion
          key={suggestion}
          className={cn(
            'rounded-full border border-border bg-card px-3.5 py-1.5',
            'text-xs text-muted-foreground transition',
            'hover:border-primary/30 hover:bg-primary/5 hover:text-foreground',
          )}
          prompt={suggestion}
          send
          method="replace"
        >
          {suggestion}
        </ThreadPrimitive.Suggestion>
      ))}
    </div>
  </div>
);

const SUGGESTIONS = [
  '贵州茅台今天涨了多少？',
  '601318 K线走势如何？',
  '比亚迪最新新闻',
];

/* ── User Message ────────────────────────────────────────────────────── */

const UserMessage: FC = () => (
  <MessagePrimitive.Root className="group/message mb-1 flex w-full min-w-0 items-start justify-end gap-2.5 sm:gap-3">
    <div className="flex min-w-0 max-w-[min(88%,calc(100%-2.5rem))] flex-col items-end space-y-1 sm:max-w-[min(78%,calc(100%-3rem))]">
      <UserMessageAttachments />
      {/* 编辑态:点击 Edit 后 composer.isEditing=true,这里渲染编辑输入框;
          ComposerPrimitive 在 message 上下文下会自动绑定到该消息的 edit composer,
          提交(Send)即覆盖原消息并重新生成。 */}
      <AuiIf condition={(s) => s.composer.isEditing}>
        <ComposerPrimitive.Root className="w-full rounded-2xl rounded-br-md border border-primary/30 bg-card shadow-sm focus-within:border-primary/45">
          <ComposerPrimitive.Input
            autoFocus
            className="min-h-16 w-full resize-none bg-transparent px-4 py-3 text-sm leading-7 text-foreground placeholder-muted-foreground focus:outline-none [overflow-wrap:anywhere]"
          />
          <div className="flex items-center justify-end gap-2 px-3 pb-3">
            <ComposerPrimitive.Cancel
              className="flex h-8 items-center rounded-lg border border-border bg-card px-3 text-xs text-foreground transition hover:bg-muted"
              title="取消编辑"
            >
              取消
            </ComposerPrimitive.Cancel>
            <ComposerPrimitive.Send
              className="flex h-8 items-center rounded-lg bg-primary px-3 text-xs text-primary-foreground transition hover:bg-primary/90 disabled:opacity-30"
              title="重新发送"
            >
              发送
            </ComposerPrimitive.Send>
          </div>
        </ComposerPrimitive.Root>
      </AuiIf>
      {/* 非编辑态:静态气泡 + Edit 按钮 */}
      <AuiIf condition={(s) => !s.composer.isEditing}>
        <div className="min-w-0 max-w-full overflow-hidden rounded-2xl rounded-br-md border border-primary/20 bg-primary px-4 py-2.5 text-sm text-primary-foreground shadow-sm [overflow-wrap:anywhere]">
          <MessagePrimitive.Parts />
        </div>
        <div className="flex items-center gap-1 px-1 opacity-0 transition-opacity group-hover/message:opacity-100">
          <ActionBarPrimitive.Edit
            className="flex size-6 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground"
            title="编辑并重新发送"
          >
            <PencilIcon className="size-3.5" />
          </ActionBarPrimitive.Edit>
          <BranchPicker />
        </div>
      </AuiIf>
    </div>
    <Avatar fallback={<UserIcon className="size-3.5" />} className="chat-avatar-user" />
  </MessagePrimitive.Root>
);

/* ── Assistant Message ───────────────────────────────────────────────── */

const AssistantMessage: FC = () => {
  const isRunning = useMessage((s) => s.status?.type === 'running');
  return (
    <MessagePrimitive.Root className="group/message mb-1 flex w-full min-w-0 items-start justify-start gap-2.5 sm:gap-3">
      <Avatar fallback={<BotIcon className="size-3.5" />} className="chat-avatar-ai" />
      <div className="min-w-0 flex-1">
        <div className="w-full min-w-0 overflow-hidden rounded-2xl rounded-bl-md border border-border bg-card/95 px-3 py-3 text-sm text-foreground shadow-[0_12px_34px_hsl(220_22%_34%/0.08)] backdrop-blur sm:px-4">
          <MessagePrimitive.Parts
            components={{
              Text: AssistantMarkdownText,
              tools: {
                by_name: {
                  get_kline: KlineToolUI,
                  get_realtime_quotes: RealtimeQuotesToolUI,
                  get_financials: FinancialsToolUI,
                  search_news: NewsToolUI,
                  get_buy_criteria_analysis: BuyCriteriaToolUI,
                },
                Fallback: GenericToolUI,
              },
            }}
          />
        </div>
        {isRunning && (
          <div className="mt-2 flex items-center gap-2 px-1 text-xs text-muted-foreground">
            <Loader2Icon className="size-3.5 animate-spin text-primary" />
            <span>正在生成回答</span>
          </div>
        )}
        <AssistantActionBar />
        <BranchPicker />
      </div>
    </MessagePrimitive.Root>
  );
};

/* ── Assistant Action Bar (Copy / Reload) ────────────────────────────── */

const AssistantActionBar: FC = () => (
  <ActionBarPrimitive.Root
    hideWhenRunning
    autohide="not-last"
    className="mt-1 flex items-center gap-0.5 px-1 opacity-0 transition-opacity group-hover/message:opacity-100 data-[copied]:opacity-100"
  >
    <ActionBarPrimitive.Copy
      copiedDuration={1500}
      className="flex size-6 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground data-[copied]:text-emerald-500"
      title="复制"
    >
      <CopyIcon className="size-3.5" />
    </ActionBarPrimitive.Copy>
    <ActionBarPrimitive.Reload
      className="flex size-6 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-30"
      title="重新生成"
    >
      <RefreshCwIcon className="size-3.5" />
    </ActionBarPrimitive.Reload>
  </ActionBarPrimitive.Root>
);

/* ── Avatar ──────────────────────────────────────────────────────────── */

const Avatar: FC<{ fallback: React.ReactNode; className?: string }> = ({
  fallback,
  className,
}) => (
  <div
    className={`flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-medium ${className ?? ''}`}
  >
    {fallback}
  </div>
);

const PROCESS_STEPS = [
  {
    marker: '正在理解问题并规划需要查询的数据...',
    label: '理解问题与规划数据',
  },
  {
    marker: '正在调用数据工具...',
    label: '调用行情与分析工具',
  },
];

function splitAssistantText(text: string) {
  const steps = PROCESS_STEPS.filter((step) => text.includes(step.marker));
  let content = text;
  for (const step of PROCESS_STEPS) {
    content = content.replaceAll(step.marker, '');
  }
  return {
    steps,
    content: content.replace(/^\s+/, '').replace(/\n{3,}/g, '\n\n'),
  };
}

const AssistantMarkdownText: FC<TextMessagePartProps> = ({ text, status }) => {
  const { steps, content } = splitAssistantText(text);
  const [expanded, setExpanded] = useState(status.type === 'running');

  return (
    <div className="w-full min-w-0 space-y-3">
      {steps.length > 0 && (
        <div className="w-full min-w-0 rounded-xl border border-border bg-muted/45">
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left"
          >
            <span className="flex min-w-0 items-center gap-2 text-sm font-medium text-foreground">
              <BrainCircuitIcon className="size-4 shrink-0 text-primary" />
              <span>Thinking</span>
            </span>
            <span className="flex items-center gap-2 text-xs text-muted-foreground">
              {status.type === 'running' ? <Loader2Icon className="size-3.5 animate-spin" /> : <CheckCircle2Icon className="size-3.5 text-emerald-500" />}
              {status.type === 'running' ? '运行中' : '完成'}
              <ChevronDownIcon className={`size-4 transition-transform ${expanded ? 'rotate-180' : ''}`} />
            </span>
          </button>

          {expanded && (
            <div className="border-t border-border px-3 py-2">
              <ol className="space-y-2">
                {steps.map((step, index) => (
                  <li key={step.marker} className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-semibold text-primary">
                      {index + 1}
                    </span>
                    <span>{step.label}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}

      {content.trim() && (
        <div className="assistant-markdown w-full min-w-0">
          <Markdown
            remarkPlugins={[remarkGfm]}
            components={{
              h1: ({ children }) => <h1 className="mb-3 mt-1 text-xl font-semibold text-foreground">{children}</h1>,
              h2: ({ children }) => <h2 className="mb-2 mt-4 text-lg font-semibold text-foreground first:mt-0">{children}</h2>,
              h3: ({ children }) => <h3 className="mb-2 mt-3 text-base font-semibold text-foreground first:mt-0">{children}</h3>,
              p: ({ children }) => <p className="my-2 leading-7 text-foreground/90">{children}</p>,
              ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
              ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
              li: ({ children }) => <li className="leading-7">{children}</li>,
              blockquote: ({ children }) => (
                <blockquote className="my-3 border-l-2 border-primary/35 bg-primary/5 py-2 pl-3 text-muted-foreground">
                  {children}
                </blockquote>
              ),
              table: ({ children }) => (
                <div className="my-3 overflow-x-auto rounded-lg border border-border bg-card/60">
                  <table className="w-max min-w-full border-collapse text-left text-[11px] sm:text-xs">
                    {children}
                  </table>
                </div>
              ),
              tr: ({ children }) => (
                <tr className="[&:last-child_td]:border-b-0">
                  {children}
                </tr>
              ),
              th: ({ children }) => (
                <th className="border-b border-border bg-muted px-3 py-2 font-semibold whitespace-nowrap text-foreground first:min-w-20 first:w-20 sm:px-3.5">
                  {children}
                </th>
              ),
              td: ({ children }) => (
                <td className="border-b border-border px-3 py-2 align-top leading-6 break-words first:min-w-20 first:w-20 first:whitespace-nowrap last:min-w-[16rem] sm:px-3.5 sm:last:min-w-[20rem]">
                  {children}
                </td>
              ),
              code: ({ children }) => (
                <code className="rounded-md border border-border bg-muted/70 px-1.5 py-0.5 font-mono text-[0.9em] text-foreground/85">
                  {children}
                </code>
              ),
              pre: ({ children }) => (
                <div className="my-3 overflow-hidden rounded-xl border border-border bg-muted/35">
                  <div className="flex items-center justify-between border-b border-border px-3 py-2">
                    <span className="text-xs font-medium text-muted-foreground">数据摘录</span>
                  </div>
                  <pre className="max-h-80 overflow-auto p-3 font-mono text-xs leading-6 text-foreground/85 [tab-size:2]">
                    {children}
                  </pre>
                </div>
              ),
              strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
            }}
          >
            {content}
          </Markdown>
        </div>
      )}
    </div>
  );
};

/* ── Branch Picker ───────────────────────────────────────────────────── */

const BranchPicker: FC = () => (
  <AuiIf condition={(s) => (s.message.branchCount ?? 1) > 1}>
    <BranchPickerPrimitive.Root className="mt-1 flex items-center gap-0.5 px-1 opacity-0 transition-opacity group-hover/message:opacity-100">
      <BranchPickerPrimitive.Previous className="flex size-5 items-center justify-center rounded text-muted-foreground hover:text-foreground">
        <ChevronLeftIcon className="size-3" />
      </BranchPickerPrimitive.Previous>
      <BranchPickerPrimitive.Number />
      <BranchPickerPrimitive.Next className="flex size-5 items-center justify-center rounded text-muted-foreground hover:text-foreground">
        <ChevronRightIcon className="size-3" />
      </BranchPickerPrimitive.Next>
    </BranchPickerPrimitive.Root>
  </AuiIf>
);

/* ── Composer ────────────────────────────────────────────────────────── */

const Composer: FC = () => {
  const isRunning = useThread((s) => s.isRunning);
  return (
    <div className="shrink-0 border-t border-border/70 bg-background/85 px-3 py-3 backdrop-blur-xl sm:px-4 sm:py-4">
      <ComposerPrimitive.Root className="relative mx-auto flex w-full max-w-4xl flex-col rounded-2xl border border-border bg-card shadow-[0_18px_50px_hsl(220_22%_34%/0.12)] transition focus-within:border-primary/45 focus-within:shadow-[0_20px_60px_hsl(var(--primary)/0.16)]">
        <ComposerAttachmentDropzone />

        <ComposerAttachments />

        <ComposerPrimitive.Input
          placeholder="问问市场、个股、板块或财务数据..."
          className="min-h-16 w-full resize-none bg-transparent px-4 pt-4 pb-2 text-[15px] leading-7 text-foreground placeholder-muted-foreground focus:outline-none sm:px-5"
          rows={1}
        />

        <div className="flex items-center justify-between gap-3 px-3 pb-3">
          <div className="flex items-center gap-2">
            <ComposerAddAttachment />
            <span className="hidden text-xs text-muted-foreground sm:inline">实时数据工具会自动按需调用</span>
          </div>

          {isRunning ? (
            <ComposerPrimitive.Cancel
              className={cn(
                'flex size-9 items-center justify-center rounded-xl',
                'border border-border bg-card text-foreground shadow-sm transition',
                'hover:bg-muted disabled:opacity-30',
              )}
              title="停止生成"
            >
              <SquareIcon className="size-3.5" />
            </ComposerPrimitive.Cancel>
          ) : (
            <ComposerPrimitive.Send
              className={cn(
                'flex size-9 items-center justify-center rounded-xl',
                'bg-primary text-primary-foreground shadow-sm transition',
                'hover:-translate-y-0.5 hover:bg-primary/90 hover:shadow-md disabled:translate-y-0 disabled:opacity-30 disabled:shadow-none',
              )}
            >
              <ArrowUpIcon className="size-4" />
            </ComposerPrimitive.Send>
          )}
        </div>
      </ComposerPrimitive.Root>
    </div>
  );
};

export default Thread;
