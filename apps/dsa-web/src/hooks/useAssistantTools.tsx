import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { makeAssistantToolUI, type ToolCallMessagePartProps } from '@assistant-ui/react';
import {
  BotIcon,
  CheckCircle2Icon,
  Loader2Icon,
  XIcon,
  XCircleIcon,
} from 'lucide-react';

/**
 * Register frontend renderers for tool calls.
 * Tool execution happens on the Python backend;
 * these components just render the call status and result in the chat UI.
 */

/* ── get_realtime_quotes ──────────────────────────────────────────────── */

const RealtimeQuotesUI = makeAssistantToolUI<{ symbols: string }, unknown>({
  toolName: 'get_realtime_quotes',
  render: ({ args, status }) => {
    if (status.type === 'running') {
      return (
        <div className="my-2 rounded-lg border border-cyan-200 bg-cyan-50 px-3 py-2 text-sm text-cyan-800">
          📊 正在查询 {args.symbols} 实时行情…
        </div>
      );
    }
    if (status.type === 'incomplete' && status.reason === 'error') {
      return (
        <div className="my-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          ❌ 查询实时行情失败
        </div>
      );
    }
    return (
      <div className="my-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
        ✅ 已获取 {args.symbols} 实时行情
      </div>
    );
  },
});

/* ── get_kline ────────────────────────────────────────────────────────── */

const KlineUI = makeAssistantToolUI<{ symbol: string; count?: number }, unknown>({
  toolName: 'get_kline',
  render: ({ args, status }) => {
    if (status.type === 'running') {
      return (
        <div className="my-2 rounded-lg border border-cyan-200 bg-cyan-50 px-3 py-2 text-sm text-cyan-800">
          📈 正在获取 {args.symbol} K线数据…
        </div>
      );
    }
    if (status.type === 'incomplete' && status.reason === 'error') {
      return (
        <div className="my-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          ❌ 获取K线数据失败
        </div>
      );
    }
    return (
      <div className="my-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
        ✅ 已获取 {args.symbol} K线数据
      </div>
    );
  },
});

/* ── get_stock_info ───────────────────────────────────────────────────── */

const StockInfoUI = makeAssistantToolUI<{ symbol: string }, unknown>({
  toolName: 'get_stock_info',
  render: ({ args, status }) => {
    if (status.type === 'running') {
      return (
        <div className="my-2 rounded-lg border border-cyan-200 bg-cyan-50 px-3 py-2 text-sm text-cyan-800">
          📋 正在查询 {args.symbol} 基本信息…
        </div>
      );
    }
    if (status.type === 'incomplete' && status.reason === 'error') {
      return (
        <div className="my-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          ❌ 查询股票信息失败
        </div>
      );
    }
    return (
      <div className="my-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
        ✅ 已获取 {args.symbol} 基本信息
      </div>
    );
  },
});

/* ── get_financials ───────────────────────────────────────────────────── */

const FinancialsUI = makeAssistantToolUI<{ symbol: string; periods?: number }, unknown>({
  toolName: 'get_financials',
  render: ({ args, status }) => {
    if (status.type === 'running') {
      return (
        <div className="my-2 rounded-lg border border-cyan-200 bg-cyan-50 px-3 py-2 text-sm text-cyan-800">
          💰 正在获取 {args.symbol} 财务数据…
        </div>
      );
    }
    if (status.type === 'incomplete' && status.reason === 'error') {
      return (
        <div className="my-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          ❌ 获取财务数据失败
        </div>
      );
    }
    return (
      <div className="my-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
        ✅ 已获取 {args.symbol} 财务数据
      </div>
    );
  },
});

/* ── search_news ──────────────────────────────────────────────────────── */

const NewsUI = makeAssistantToolUI<{ symbol: string; days?: number }, unknown>({
  toolName: 'search_news',
  render: ({ args, status }) => {
    if (status.type === 'running') {
      return (
        <div className="my-2 rounded-lg border border-cyan-200 bg-cyan-50 px-3 py-2 text-sm text-cyan-800">
          📰 正在搜索 {args.symbol} 相关新闻…
        </div>
      );
    }
    if (status.type === 'incomplete' && status.reason === 'error') {
      return (
        <div className="my-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          ❌ 搜索新闻失败
        </div>
      );
    }
    return (
      <div className="my-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
        ✅ 已获取 {args.symbol} 相关新闻
      </div>
    );
  },
});

/* ── Generic fallback ────────────────────────────────────────────────── */

const TOOL_LABELS: Record<string, string> = {
  get_realtime_quotes: '实时行情',
  get_kline: 'K线数据',
  get_history_data: '历史行情',
  get_market_status: '大盘状态',
  get_sector_list: '板块列表',
  get_stock_info: '股票信息',
  get_financials: '财务摘要',
  get_balance_sheet: '资产负债表',
  get_income_statement: '利润表',
  get_cashflow: '现金流量表',
  get_valuation_ratios: '估值指标',
  get_price_overdraft_signal: '股价透支判定',
  get_shareholder_structure: '股东结构',
  search_news: '新闻搜索',
  get_risk_events: '风险事件',
  get_sentiment: '舆情数据',
  get_announcements: '公告查询',
  get_research_report: '研报查询',
  get_social_sentiment: '社交舆情',
  get_index_data: '指数行情',
  get_bond_yield: '债券收益率',
  get_macro_indicator: '宏观指标',
  get_sector_flow: '板块资金流',
  get_market_breadth: '市场宽度',
};

function stringifyCompact(value: unknown): string {
  if (value === undefined || value === null) {
    return '';
  }
  if (typeof value === 'string') {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function truncateDisplay(text: string, maxLength = 5000): string {
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength)}\n...[内容已截断]`;
}

function getToolError(result: unknown): string {
  if (!result || typeof result !== 'object') {
    return stringifyCompact(result);
  }
  const maybeError = (result as { error?: unknown }).error;
  return stringifyCompact(maybeError || result);
}

function getStatusError(statusError: unknown): string {
  if (statusError instanceof Error) {
    return statusError.message;
  }
  return stringifyCompact(statusError);
}

const GenericToolUI = ({
  toolName,
  args,
  argsText,
  result,
  isError,
  status,
}: ToolCallMessagePartProps<Record<string, unknown>, unknown>) => {
  const [drawerState, setDrawerState] = useState<'closed' | 'open' | 'closing'>('closed');
  const label = TOOL_LABELS[toolName] || toolName;
  const argsDisplay = stringifyCompact(args) || argsText;
  const failed = isError || (status.type === 'incomplete' && status.reason === 'error');
  const statusText = status.type === 'running' ? 'running' : failed ? 'failed' : 'complete';
  const resultDisplay =
    failed
      ? getToolError(result) || getStatusError(status.type === 'incomplete' ? status.error : undefined)
      : stringifyCompact(result);
  const tone = failed
    ? 'border-red-300 bg-red-50 text-red-700'
    : status.type === 'running'
      ? 'border-blue-300 bg-blue-50 text-blue-700'
      : 'border-blue-300 bg-blue-50 text-blue-700';
  const StatusIcon = failed ? XCircleIcon : status.type === 'running' ? Loader2Icon : CheckCircle2Icon;
  const open = drawerState !== 'closed';

  useEffect(() => {
    if (drawerState !== 'closing') {
      return undefined;
    }
    const timeout = window.setTimeout(() => setDrawerState('closed'), 180);
    return () => window.clearTimeout(timeout);
  }, [drawerState]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setDrawerState('closing');
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open]);

  const drawer = open && typeof document !== 'undefined' ? createPortal(
    <div
      className={`tool-drawer-overlay fixed inset-0 z-50 flex justify-end bg-slate-950/30 backdrop-blur-[1px] ${
        drawerState === 'closing' ? 'tool-drawer-overlay-out' : ''
      }`}
    >
      <button
        type="button"
        aria-label="关闭工具详情"
        className="absolute inset-0 cursor-default"
        onClick={() => setDrawerState('closing')}
      />
      <aside
        className={`tool-drawer-panel relative flex h-full w-full max-w-xl flex-col border-l border-border bg-background shadow-2xl ${
          drawerState === 'closing' ? 'tool-drawer-panel-out' : ''
        }`}
      >
        <div className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
          <div className="min-w-0">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Tool Call</div>
            <h3 className="mt-1 truncate text-base font-semibold text-foreground">Call Tool execute - {label}</h3>
            <p className="mt-1 break-all text-xs text-muted-foreground">{toolName}</p>
          </div>
          <button
            type="button"
            onClick={() => setDrawerState('closing')}
            className="flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition hover:bg-muted hover:text-foreground"
            title="关闭"
          >
            <XIcon className="size-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 text-xs text-muted-foreground">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <div className="font-medium text-foreground">Tool</div>
              <code className="mt-1 block break-words rounded-md bg-muted px-3 py-2 text-[11px] text-foreground">
                {toolName}
              </code>
            </div>
            <div>
              <div className="font-medium text-foreground">Status</div>
              <code className="mt-1 block rounded-md bg-muted px-3 py-2 text-[11px] text-foreground">
                {statusText}
              </code>
            </div>
          </div>

          <div className="mt-4">
            <div className="font-medium text-foreground">Arguments</div>
            <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-3 text-[11px] leading-relaxed text-foreground">
              {truncateDisplay(argsDisplay || '{}')}
            </pre>
          </div>

          <div className="mt-4">
            <div className="font-medium text-foreground">{failed ? 'Error' : 'Result'}</div>
            <pre className="mt-1 max-h-[55vh] overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-3 text-[11px] leading-relaxed text-foreground">
              {truncateDisplay(resultDisplay || (status.type === 'running' ? '等待工具返回...' : '无返回内容'))}
            </pre>
          </div>
        </div>
      </aside>
    </div>,
    document.body,
  ) : null;

  return (
    <div className="my-2 max-w-full overflow-hidden">
      <button
        type="button"
        onClick={() => setDrawerState('open')}
        className={`inline-flex max-w-full items-center gap-2 overflow-hidden rounded-full border px-3 py-1.5 text-left text-sm shadow-sm transition hover:-translate-y-0.5 hover:bg-white hover:shadow-md ${tone}`}
      >
        <BotIcon className="size-4 shrink-0" />
        <span className="min-w-0 truncate">Call Tool execute - {label}</span>
        <StatusIcon className={`size-4 shrink-0 ${status.type === 'running' ? 'animate-spin' : ''}`} />
      </button>

      {drawer}
    </div>
  );
};

/**
 * Register all tool UIs. Call inside AssistantRuntimeProvider.
 */
export function useAssistantTools() {
  // Each makeAssistantToolUI returns a component; we render them as children
  // of the assistant message so they appear inline.
  // The components are mounted but render null unless a matching tool call exists.
}

export {
  RealtimeQuotesUI,
  KlineUI,
  StockInfoUI,
  FinancialsUI,
  NewsUI,
  GenericToolUI,
};
