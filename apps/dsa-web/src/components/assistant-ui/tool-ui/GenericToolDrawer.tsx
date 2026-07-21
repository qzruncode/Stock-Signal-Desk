import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import {
  BotIcon,
  CheckCircle2Icon,
  Loader2Icon,
  XIcon,
  XCircleIcon,
} from 'lucide-react';
import { TOOL_LABELS } from '../../../utils/toolLabels';

/** 其余未做内联可视化的工具(~20 个)统一走这个抽屉:点击药丸看 args/result JSON。 */

function stringifyCompact(value: unknown): string {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function getToolError(result: unknown): string {
  if (!result || typeof result !== 'object') return stringifyCompact(result);
  const maybeError = (result as { error?: unknown }).error;
  return stringifyCompact(maybeError || result);
}

function getStatusError(statusError: unknown): string {
  if (statusError instanceof Error) return statusError.message;
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
  const statusText = status.type === 'running' ? '运行中' : failed ? '失败' : '已完成';
  const resultDisplay = failed
    ? getToolError(result) || getStatusError(status.type === 'incomplete' ? status.error : undefined)
    : stringifyCompact(result);
  const tone = failed
    ? 'border-red-300 bg-red-50 text-red-700'
    : 'border-blue-300 bg-blue-50 text-blue-700';
  const StatusIcon = failed ? XCircleIcon : status.type === 'running' ? Loader2Icon : CheckCircle2Icon;
  const open = drawerState !== 'closed';

  useEffect(() => {
    if (drawerState !== 'closing') return undefined;
    const timeout = window.setTimeout(() => setDrawerState('closed'), 180);
    return () => window.clearTimeout(timeout);
  }, [drawerState]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setDrawerState('closing');
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open]);

  const drawer =
    open && typeof document !== 'undefined' ? (
      createPortal(
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
                <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">研究工具详情</div>
                <h3 className="mt-1 truncate text-base font-semibold text-foreground">{label}</h3>
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

            <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-5 py-4 text-xs text-muted-foreground">
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <div className="font-medium text-foreground">工具</div>
                  <code className="mt-1 block break-words rounded-md bg-muted px-3 py-2 text-[11px] text-foreground">{toolName}</code>
                </div>
                <div>
                  <div className="font-medium text-foreground">状态</div>
                  <code className="mt-1 block rounded-md bg-muted px-3 py-2 text-[11px] text-foreground">{statusText}</code>
                </div>
              </div>

              <div className="mt-4">
                <div className="font-medium text-foreground">查询参数</div>
                <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-muted p-3 text-[11px] leading-relaxed text-foreground">
                  {argsDisplay || '{}'}
                </pre>
              </div>

              <div className="mt-4">
                <div className="font-medium text-foreground">{failed ? '错误信息' : '完整结果'}</div>
                <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-muted p-3 text-[11px] leading-relaxed text-foreground">
                  {resultDisplay || (status.type === 'running' ? '等待工具返回...' : '无返回内容')}
                </pre>
              </div>
            </div>
          </aside>
        </div>,
        document.body,
      )
    ) : null;

  return (
    <div className="my-0.5 max-w-full overflow-hidden">
      <button
        type="button"
        onClick={() => setDrawerState('open')}
        className={`inline-flex max-w-full items-center gap-1 overflow-hidden rounded border px-1.5 py-0.5 text-left text-[10px] font-medium leading-3 transition hover:bg-white ${tone}`}
      >
        <BotIcon className="size-2.5 shrink-0" />
        <span className="min-w-0 truncate">{status.type === 'running' ? `正在查询 · ${label}` : label}</span>
        <StatusIcon className={`size-2.5 shrink-0 ${status.type === 'running' ? 'animate-spin' : ''}`} />
      </button>
      {drawer}
    </div>
  );
};

export default GenericToolUI;
