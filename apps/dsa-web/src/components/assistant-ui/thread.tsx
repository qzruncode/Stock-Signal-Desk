import type { FC } from 'react';
import { useEffect, useRef, useState } from 'react';
import {
  AuiIf,
  ThreadPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  BranchPickerPrimitive,
  ActionBarPrimitive,
  useMessage,
  useThread,
  type ReasoningMessagePartProps,
} from '@assistant-ui/react';
import {
  ArrowUpIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ChevronDownIcon,
  BrainCircuitIcon,
  CheckCircle2Icon,
  DatabaseIcon,
  FileSearchIcon,
  GitCompareArrowsIcon,
  Loader2Icon,
  SparklesIcon,
  CopyIcon,
  RefreshCwIcon,
  PencilIcon,
  DownloadIcon,
  Volume2Icon,
  SquareIcon as StopIcon,
  MicIcon,
  Maximize2Icon,
  Minimize2Icon,
} from 'lucide-react';
import {
  GenericToolUI,
  KlineToolUI,
  RealtimeQuotesToolUI,
  FinancialsToolUI,
  NewsToolUI,
  RssFeedToolUI,
  FinancialArticleToolUI,
  FinancialExportToolUI,
  FinancialFeedToolUI,
  FinancialSourcesToolUI,
  WorkflowToolsUI,
} from '../../hooks/useAssistantTools';
import { ASSISTANT_SUGGESTION_GROUPS } from '../../utils/assistantQuickActions';
import {
  ComposerAttachments,
  ComposerAddAttachment,
  ComposerAttachmentDropzone,
  UserMessageAttachments,
} from './attachment';
import { Tooltip } from '../common/Tooltip';
import { AssistantMarkdownText } from './AssistantMarkdownText';
import { splitAssistantText } from '../../utils/assistantTextSplit';
import { cn } from '../../utils/cn';

/* ── Thread (root) ───────────────────────────────────────────────────── */

const Thread: FC<{ onUserCancel?: () => void }> = ({ onUserCancel }) => {
  return (
    <ThreadPrimitive.Root className="flex h-full min-h-0 flex-col overflow-hidden">
      <ThreadPrimitive.Viewport className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto bg-[linear-gradient(180deg,hsl(var(--background)),hsl(var(--background)))] px-3 pt-14 pb-4 sm:gap-4 sm:px-4 sm:pt-5 sm:pb-5 lg:px-6">
        <AuiIf condition={(s) => s.thread.isEmpty}>
          <EmptyState />
        </AuiIf>

        <AuiIf condition={(s) => !s.thread.isEmpty}>
          <div className="mx-auto w-full max-w-3xl">
            <ThreadPrimitive.Messages
              components={{
                UserMessage,
                AssistantMessage,
              }}
            />
          </div>
        </AuiIf>

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

      <Composer onUserCancel={onUserCancel} />
    </ThreadPrimitive.Root>
  );
};

/* ── Empty State ─────────────────────────────────────────────────────── */

const EmptyState: FC = () => (
  <div className="mx-auto flex h-full min-h-0 w-full max-w-4xl flex-1 flex-col items-center overflow-hidden px-2 pb-1 pt-1 text-center sm:pt-4">
    <div className="hidden size-12 items-center justify-center rounded-2xl border border-primary/20 bg-card text-primary shadow-[0_18px_50px_hsl(var(--primary)/0.14)] sm:flex">
      <SparklesIcon className="size-6" />
    </div>
    <div className="mt-1 sm:mt-3">
      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-primary sm:text-xs sm:tracking-[0.18em]">A-SHARE RESEARCH AGENT</p>
      <h2 className="mt-1 text-xl font-semibold tracking-tight text-foreground sm:mt-2 sm:text-3xl">把问题交给会查数据的投研助手</h2>
      <p className="mx-auto mt-1 hidden max-w-2xl text-sm leading-6 text-muted-foreground sm:block">
        支持产业链研究、公司比较、财务与估值核验、行情和事件追踪。回答会保留数据时间、来源与风险边界，并能沿着上一轮继续追问。
      </p>
    </div>

    <div className="mt-3 grid w-full grid-cols-3 gap-1.5 text-left sm:mt-5 sm:gap-3">
      {CAPABILITIES.map(({ title, description, icon: Icon }) => (
        <div key={title} className="rounded-lg border border-border/70 bg-card/70 px-2 py-2 sm:rounded-2xl sm:border-border/80 sm:bg-card/80 sm:p-3.5 sm:shadow-sm">
          <div className="flex min-w-0 flex-col items-center gap-1 text-center sm:flex-row sm:gap-2.5 sm:text-left">
            <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary sm:size-8 sm:rounded-xl">
              <Icon className="size-3.5 sm:size-4" />
            </div>
            <p className="min-w-0 text-center text-[11px] font-semibold leading-4 text-foreground sm:text-left sm:text-sm">{title}</p>
          </div>
          <p className="mt-2 hidden text-xs leading-5 text-muted-foreground sm:block">{description}</p>
        </div>
      ))}
    </div>

    <div className="mt-3 flex min-h-0 w-full flex-1 flex-col text-left sm:mt-5">
      <p className="mb-2 shrink-0 px-1 text-xs font-medium text-muted-foreground">你可以这样问</p>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-2 [scrollbar-gutter:stable] [scrollbar-width:thin]">
        {ASSISTANT_SUGGESTION_GROUPS.map((group) => (
          <section key={group.id} aria-labelledby={`suggestion-group-${group.id}`}>
            <div className="mb-1.5 flex min-w-0 items-baseline gap-2 px-1">
              <h3 id={`suggestion-group-${group.id}`} className="shrink-0 text-[11px] font-semibold text-foreground">
                {group.title}
              </h3>
              <span className="min-w-0 truncate text-[10px] text-muted-foreground">{group.description}</span>
            </div>
            <div className="grid gap-1.5 min-[520px]:grid-cols-2">
              {group.items.map((suggestion) => (
                <SuggestionCard
                  key={suggestion.label}
                  label={suggestion.label}
                  prompt={suggestion.prompt}
                />
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  </div>
);

const SuggestionCard: FC<{ label: string; prompt: string }> = ({ label, prompt }) => {
  const cardRef = useRef<HTMLSpanElement | null>(null);
  const [isTruncated, setIsTruncated] = useState(false);

  useEffect(() => {
    const button = cardRef.current?.querySelector('button');
    if (!button) return;

    const updateTruncation = () => {
      setIsTruncated(button.scrollWidth > button.clientWidth);
    };
    updateTruncation();

    const observer = new ResizeObserver(updateTruncation);
    observer.observe(button);
    return () => observer.disconnect();
  }, [isTruncated, label]);

  const card = (
    <span ref={cardRef} className="block min-w-0 w-full">
      <ThreadPrimitive.Suggestion
        className={cn(
          'block w-full min-w-0 !truncate rounded-xl border border-border bg-card px-3 py-1.5 text-left',
          '!text-[11px] !leading-4 text-foreground/85 shadow-sm transition',
          'hover:-translate-y-0.5 hover:border-primary/30 hover:bg-primary/5 hover:text-foreground hover:shadow-md',
        )}
        prompt={prompt}
        clearComposer
      >
        {label}
      </ThreadPrimitive.Suggestion>
    </span>
  );

  if (!isTruncated) return card;

  return (
    <Tooltip content={label} className="min-w-0 w-full" contentClassName="min-w-0 whitespace-normal">
      {card}
    </Tooltip>
  );
};

const CAPABILITIES = [
  { title: '多源数据核验', description: '行情、财务、公告、研报与新闻按问题自动组合。', icon: DatabaseIcon },
  { title: '产业链研究', description: '拆解受益环节、兑现路径、催化与主要反证。', icon: FileSearchIcon },
  { title: '连续比较追问', description: '沿用本次研究上下文继续映射公司和比较标的。', icon: GitCompareArrowsIcon },
];

/* ── User Message ────────────────────────────────────────────────────── */

const UserMessage: FC = () => (
  <MessagePrimitive.Root className="group/message mb-1.5 flex w-full min-w-0 items-start justify-end">
    <div className="relative flex min-w-0 max-w-[82%] flex-col items-end pb-5 sm:max-w-[68%]">
      <UserMessageAttachments />
      {/* 编辑态:点击 Edit 后 composer.isEditing=true,这里渲染编辑输入框;
          ComposerPrimitive 在 message 上下文下会自动绑定到该消息的 edit composer,
          提交(Send)即覆盖原消息并重新生成。 */}
      <AuiIf condition={(s) => s.composer.isEditing}>
        <ComposerPrimitive.Root className="w-full rounded-2xl rounded-br-md border border-primary/30 bg-card shadow-sm focus-within:border-primary/45">
          <ComposerPrimitive.Input
            autoFocus
            className="min-h-14 w-full resize-none bg-transparent px-4 py-3 text-[13px] leading-5 text-foreground placeholder-muted-foreground focus:outline-none [overflow-wrap:anywhere]"
            style={{ fontSize: '12px', lineHeight: '18px' }}
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
        <div className="min-w-0 max-w-full overflow-hidden rounded-xl rounded-br-md bg-primary/[0.06] px-3 py-2 text-sm leading-6 text-foreground [overflow-wrap:anywhere]">
          <MessagePrimitive.Parts />
        </div>
        <div className="absolute bottom-0 right-0 flex h-5 items-center gap-0.5 opacity-0 transition-opacity group-hover/message:opacity-100">
          <ActionBarPrimitive.Edit
            className="flex size-5 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground"
            title="编辑并重新发送"
          >
            <PencilIcon className="size-3" />
          </ActionBarPrimitive.Edit>
          <BranchPicker />
        </div>
      </AuiIf>
    </div>
  </MessagePrimitive.Root>
);

/* ── Assistant Message ───────────────────────────────────────────────── */

const AssistantMessage: FC = () => {
  const isRunning = useMessage((s) => s.status?.type === 'running');
  const hasVisibleContent = useMessage((s) =>
    s.content.some((part) => {
      if (part.type === 'text') {
        return splitAssistantText(part.text).content.trim().length > 0;
      }
      if (part.type === 'reasoning') {
        return part.text.trim().length > 0;
      }
      return true;
    }),
  );
  return (
    <MessagePrimitive.Root className="group/message mb-1.5 flex w-full min-w-0 items-start justify-start">
      <div className="relative min-w-0 flex-1 pb-5">
        <div className="w-full min-w-0 overflow-hidden rounded-xl bg-card/75 px-3.5 py-3 text-sm text-foreground sm:px-4 sm:py-3.5">
          {isRunning && !hasVisibleContent && <AssistantPendingIndicator />}
          <MessagePrimitive.Parts
            components={{
              Text: AssistantMarkdownText,
              Reasoning: AssistantReasoning,
              tools: {
                by_name: {
                  get_kline: KlineToolUI,
                  get_realtime_quotes: RealtimeQuotesToolUI,
                  get_financials: FinancialsToolUI,
                  search_news: NewsToolUI,
                  search_financial_news: RssFeedToolUI,
                  search_research_library: RssFeedToolUI,
                  list_financial_sources: FinancialSourcesToolUI,
                  inspect_financial_source: FinancialSourcesToolUI,
                  read_financial_feed: FinancialFeedToolUI,
                  transform_webpage_to_feed: FinancialFeedToolUI,
                  read_financial_article: FinancialArticleToolUI,
                  export_financial_feed: FinancialExportToolUI,
                  manage_watchlist: WorkflowToolsUI,
                  manage_watchlist_groups: WorkflowToolsUI,
                  run_stock_analysis: WorkflowToolsUI,
                  get_analysis_status: WorkflowToolsUI,
                  search_analysis_history: WorkflowToolsUI,
                  read_analysis_report: WorkflowToolsUI,
                  delete_analysis_history: WorkflowToolsUI,
                  manage_analysis_templates: WorkflowToolsUI,
                  run_batch_analysis: WorkflowToolsUI,
                  manage_batch_run: WorkflowToolsUI,
                  manage_analysis_schedule: WorkflowToolsUI,
                  get_notification_status: WorkflowToolsUI,
                  send_notification: WorkflowToolsUI,
                },
                Fallback: GenericToolUI,
              },
            }}
          />
        </div>
        <div className="absolute bottom-0 left-0 flex h-5 items-center gap-1">
          <AssistantActionBar />
          <BranchPicker />
        </div>
      </div>
    </MessagePrimitive.Root>
  );
};

/* ── Assistant Action Bar (Copy / Reload) ────────────────────────────── */

const AssistantActionBar: FC = () => (
  <ActionBarPrimitive.Root
    hideWhenRunning
    autohide="not-last"
    className="flex h-5 items-center gap-0.5 opacity-0 transition-opacity group-hover/message:opacity-100 data-[copied]:opacity-100"
  >
    <ActionBarPrimitive.Copy
      copiedDuration={1500}
      className="flex size-5 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground data-[copied]:text-emerald-500"
      title="复制"
    >
      <CopyIcon className="size-3" />
    </ActionBarPrimitive.Copy>
    {/* 朗读回答(TTS):需在 runtime adapters 配 speech 合成器(见 ChatHomePage)。
        未配置或无文本时 Speak 自动隐藏;朗读中(s.message.speech 存在)显示 Stop。 */}
    <ActionBarPrimitive.Speak
      className="flex size-5 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-30"
      title="朗读"
    >
      <Volume2Icon className="size-3" />
    </ActionBarPrimitive.Speak>
    <AuiIf condition={(s) => s.message.speech != null}>
      <ActionBarPrimitive.StopSpeaking
        className="flex size-5 items-center justify-center rounded text-primary transition hover:bg-primary/10"
        title="停止朗读"
      >
        <StopIcon className="size-3" />
      </ActionBarPrimitive.StopSpeaking>
    </AuiIf>
    {/* 导出回答为 Markdown 文件(纯前端,无后端)。filename 带对话上下文更友好。 */}
    <ActionBarPrimitive.ExportMarkdown
      className="flex size-5 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-30"
      title="导出 Markdown"
    >
      <DownloadIcon className="size-3" />
    </ActionBarPrimitive.ExportMarkdown>
    <ActionBarPrimitive.Reload
      className="flex size-5 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-30"
      title="重新生成"
    >
      <RefreshCwIcon className="size-3" />
    </ActionBarPrimitive.Reload>
  </ActionBarPrimitive.Root>
);

const AssistantPendingIndicator: FC = () => (
  <div className="flex items-center gap-2 text-sm text-muted-foreground">
    <Loader2Icon className="size-4 animate-spin text-primary" />
    <span>正在思考</span>
    <span className="inline-flex items-center gap-0.5" aria-hidden="true">
      <span className="size-1 animate-bounce rounded-full bg-primary/70 [animation-delay:-0.24s]" />
      <span className="size-1 animate-bounce rounded-full bg-primary/70 [animation-delay:-0.12s]" />
      <span className="size-1 animate-bounce rounded-full bg-primary/70" />
    </span>
  </div>
);

const AssistantReasoning: FC<ReasoningMessagePartProps> = ({ text, status }) => {
  const [expanded, setExpanded] = useState(true);

  if (!text.trim()) return null;

  return (
    <div className="mb-3 w-full min-w-0 rounded-xl border border-primary/15 bg-primary/[0.035]">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left"
        aria-expanded={expanded}
      >
        <span className="flex min-w-0 items-center gap-2 text-sm font-medium text-foreground">
          <BrainCircuitIcon className="size-4 shrink-0 text-primary" />
          <span>模型思考</span>
        </span>
        <span className="flex items-center gap-2 text-xs text-muted-foreground">
          {status.type === 'running' ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : (
            <CheckCircle2Icon className="size-3.5 text-emerald-500" />
          )}
          {status.type === 'running' ? '思考中' : '完成'}
          <ChevronDownIcon className={`size-4 transition-transform ${expanded ? 'rotate-180' : ''}`} />
        </span>
      </button>

      {expanded && (
        <div className="border-t border-primary/10 px-3 py-2.5">
          <div className="whitespace-pre-wrap break-words text-xs leading-6 text-muted-foreground">
            {text}
          </div>
        </div>
      )}
    </div>
  );
};

/* ── Branch Picker ───────────────────────────────────────────────────── */

const BranchPicker: FC = () => (
  <AuiIf condition={(s) => (s.message.branchCount ?? 1) > 1}>
    <BranchPickerPrimitive.Root className="flex h-6 items-center gap-0.5 opacity-0 transition-opacity group-hover/message:opacity-100">
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

const Composer: FC<{ onUserCancel?: () => void }> = ({ onUserCancel }) => {
  const isRunning = useThread((s) => s.isRunning);
  const [isExpanded, setIsExpanded] = useState(false);
  return (
    <div className="shrink-0 border-t border-border/70 bg-background/90 px-3 py-3 backdrop-blur-xl sm:px-4 sm:py-4">
      <ComposerPrimitive.Root className="group/composer relative mx-auto flex w-full max-w-3xl flex-col rounded-xl border border-border/80 bg-card/90 shadow-[0_8px_28px_hsl(220_22%_34%/0.06)] transition focus-within:border-primary/35 focus-within:shadow-[0_10px_32px_hsl(var(--primary)/0.08)]">
        <ComposerAttachmentDropzone />

        <ComposerAttachments />

        {/* 语音输入实时转写预览:录音中在输入框上方显示部分识别结果。 */}
        <AuiIf condition={(s) => s.composer.dictation != null}>
          <div className="flex items-center gap-2 px-3 pt-2 text-xs text-muted-foreground sm:px-4">
            <span className="size-1.5 animate-pulse rounded-full bg-red-500" />
            <ComposerPrimitive.DictationTranscript className="min-w-0 truncate" />
          </div>
        </AuiIf>

        <ComposerPrimitive.Input
          placeholder="问问市场、个股、板块或财务数据..."
          className={cn(
            'w-full resize-none bg-transparent px-3 pt-2.5 pb-1.5 pr-10 text-xs leading-5 text-foreground placeholder-muted-foreground focus:outline-none sm:px-4 sm:pr-10',
            isExpanded ? 'min-h-32 max-h-56 overflow-y-auto' : 'min-h-11 overflow-hidden',
          )}
          rows={isExpanded ? 6 : 1}
          style={{ fontSize: '12px', lineHeight: '18px' }}
        />
        <button
          type="button"
          onClick={() => setIsExpanded((value) => !value)}
          className="absolute right-2 top-2 flex size-7 items-center justify-center rounded-md text-muted-foreground opacity-0 transition hover:bg-muted hover:text-foreground focus:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/35 group-hover/composer:opacity-100 group-focus-within/composer:opacity-100"
          title={isExpanded ? '收起输入框' : '展开输入框'}
          aria-label={isExpanded ? '收起输入框' : '展开输入框'}
          aria-pressed={isExpanded}
        >
          {isExpanded ? <Minimize2Icon className="size-3" /> : <Maximize2Icon className="size-3" />}
        </button>

        <div className="flex items-center justify-between gap-3 px-2.5 pb-2.5">
          <div className="flex items-center gap-1.5">
            <ComposerAddAttachment />
            {/* 语音输入:无 DictationAdapter(浏览器不支持)或非编辑态时 Dictate 自动隐藏;
                录音中显示 StopDictation(红点)+ 实时转写预览。 */}
            <ComposerPrimitive.Dictate
              className="flex size-8 items-center justify-center rounded-lg text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-30"
              title="语音输入"
            >
              <MicIcon className="size-3.5" />
            </ComposerPrimitive.Dictate>
            <AuiIf condition={(s) => s.composer.dictation != null}>
              <ComposerPrimitive.StopDictation
                className="relative flex size-8 items-center justify-center rounded-lg text-red-500 transition hover:bg-red-50"
                title="停止语音输入"
              >
                <MicIcon className="size-3.5" />
                <span className="absolute right-1.5 top-1.5 size-1.5 animate-pulse rounded-full bg-red-500" />
              </ComposerPrimitive.StopDictation>
            </AuiIf>
            <span className="hidden text-xs text-muted-foreground sm:inline">实时数据工具会自动按需调用</span>
          </div>

          {isRunning ? (
            <ComposerPrimitive.Cancel
              onClick={onUserCancel}
              className={cn(
                'flex size-8 items-center justify-center rounded-lg',
                'border border-border bg-card text-foreground shadow-sm transition',
                'hover:bg-muted disabled:opacity-30',
              )}
              title="停止生成"
            >
              <StopIcon className="size-3" />
            </ComposerPrimitive.Cancel>
          ) : (
            <ComposerPrimitive.Send
              className={cn(
                'flex size-8 items-center justify-center rounded-lg',
                'bg-primary/90 text-primary-foreground shadow-sm transition',
                'hover:bg-primary disabled:opacity-30 disabled:shadow-none',
              )}
            >
              <ArrowUpIcon className="size-3.5" />
            </ComposerPrimitive.Send>
          )}
        </div>
      </ComposerPrimitive.Root>
      <p className="mx-auto mt-1.5 max-w-3xl text-center text-[10px] leading-4 text-muted-foreground/80">
        AI 可能出错，关键投资事实请结合原始公告与数据来源复核；内容不构成投资建议。
      </p>
    </div>
  );
};

export default Thread;
