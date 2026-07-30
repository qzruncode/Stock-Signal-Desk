import type { FC, ReactNode } from 'react';
import { createContext, useCallback, useContext, useEffect, useId, useMemo, useState } from 'react';
import { useToolArgsStatus, type ToolCallMessagePartProps } from '@assistant-ui/react';
import { CheckCircle2Icon, ChevronDownIcon, Loader2Icon, XCircleIcon } from 'lucide-react';
import { cn } from '../../../utils/cn';

type ToolStatusItem = { id: string; label: string; running: boolean; failed: boolean };

type ToolStatusGroupValue = {
  items: ToolStatusItem[];
  expanded: boolean;
  register: (item: ToolStatusItem) => void;
  unregister: (id: string) => void;
  setExpanded: (expanded: boolean) => void;
};

const ToolStatusGroupContext = createContext<ToolStatusGroupValue | null>(null);
const COLLAPSED_TOOL_STATUS_COUNT = 3;

export const ToolStatusGroupProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const [items, setItems] = useState<ToolStatusItem[]>([]);
  const [expanded, setExpanded] = useState(false);
  const register = useCallback((item: ToolStatusItem) => {
    setItems((current) => {
      const index = current.findIndex((entry) => entry.id === item.id);
      if (index === -1) return [...current, item];
      const next = [...current];
      next[index] = item;
      return next;
    });
  }, []);
  const unregister = useCallback((id: string) => {
    setItems((current) => current.filter((entry) => entry.id !== id));
  }, []);
  const value = useMemo<ToolStatusGroupValue>(() => ({
    items,
    expanded,
    register,
    unregister,
    setExpanded,
  }), [expanded, items, register, unregister]);

  return (
    <ToolStatusGroupContext.Provider value={value}>
      {children}
    </ToolStatusGroupContext.Provider>
  );
};

/**
 * 工具调用的运行态药丸(loading/complete/error)。
 * 各内联工具 UI 在 result 尚未返回时统一用它表示进度。
 *
 * streamingFields:药丸上展示的参数字段名(如 'symbol')。通过 useToolArgsStatus
 * 读取这些字段的流式状态——若任一字段仍 'streaming',追加"识别中"动效提示,
 * 避免 args 还在流式传输时把半截值当成最终值展示。
 */
export const ToolStatusPill: FC<{
  status: ToolCallMessagePartProps['status'];
  isError?: boolean;
  label: string;
  icon?: ReactNode;
  streamingFields?: ReadonlyArray<string>;
}> = ({ status, isError, label, icon, streamingFields }) => {
  const id = useId();
  const group = useContext(ToolStatusGroupContext);
  const register = group?.register;
  const unregister = group?.unregister;
  const failed = isError || (status.type === 'incomplete' && status.reason === 'error');
  const running = status.type === 'running';
  // useToolArgsStatus 只能在工具调用 part 内调用;非 running 时也安全(返回 complete)。
  const { propStatus } = useToolArgsStatus();
  const argsStreaming =
    running
    && !!streamingFields
    && streamingFields.some((field) => propStatus[field as keyof typeof propStatus] === 'streaming');
  const Icon = failed ? XCircleIcon : running ? Loader2Icon : CheckCircle2Icon;
  const tone = failed
    ? 'border-red-200 bg-red-50 text-red-700'
    : running
      ? 'border-cyan-200 bg-cyan-50 text-cyan-700'
      : 'border-emerald-200 bg-emerald-50 text-emerald-700';
  useEffect(() => {
    register?.({ id, label, running, failed });
    return () => unregister?.(id);
  }, [failed, id, label, register, running, unregister]);

  const index = group?.items.findIndex((item) => item.id === id) ?? -1;
  const hiddenCount = group ? Math.max(0, group.items.length - COLLAPSED_TOOL_STATUS_COUNT) : 0;
  const collapsed = !!group && !group.expanded && index >= COLLAPSED_TOOL_STATUS_COUNT;
  if (collapsed) return null;

  return (
    <>
      <div className={cn('my-0.5 mr-1.5 inline-flex max-w-full items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-medium leading-4', tone)}>
        {icon ?? <Icon className={cn('size-3 shrink-0', running && 'animate-spin')} />}
        <span className="min-w-0 truncate">{label}</span>
        {argsStreaming && (
          <span className="ml-0.5 inline-flex items-center gap-0.5 rounded bg-cyan-100/70 px-1 py-0 text-[10px] text-cyan-700">
            <span className="size-1 animate-pulse rounded-full bg-cyan-500" />
            识别中
          </span>
        )}
      </div>
      {!!group && index === COLLAPSED_TOOL_STATUS_COUNT - 1 && hiddenCount > 0 && !group.expanded && (
        <button
          type="button"
          onClick={() => group.setExpanded(true)}
          className="my-0.5 mr-1.5 inline-flex items-center gap-1 rounded-md border border-border bg-muted/60 px-2 py-1 text-[11px] font-medium leading-4 text-muted-foreground transition hover:bg-muted hover:text-foreground"
        >
          还有 {hiddenCount} 个
          <ChevronDownIcon className="size-3" />
        </button>
      )}
      {!!group && index === group.items.length - 1 && group.expanded && hiddenCount > 0 && (
        <button
          type="button"
          onClick={() => group.setExpanded(false)}
          className="my-0.5 mr-1.5 inline-flex items-center gap-1 rounded-md border border-border bg-muted/60 px-2 py-1 text-[11px] font-medium leading-4 text-muted-foreground transition hover:bg-muted hover:text-foreground"
        >
          收起
          <ChevronDownIcon className="size-3 rotate-180" />
        </button>
      )}
    </>
  );
};
