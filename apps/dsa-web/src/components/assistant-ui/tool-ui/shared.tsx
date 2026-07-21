import type { FC, ReactNode } from 'react';
import { useToolArgsStatus, type ToolCallMessagePartProps } from '@assistant-ui/react';
import { CheckCircle2Icon, Loader2Icon, XCircleIcon } from 'lucide-react';
import { cn } from '../../../utils/cn';

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
  return (
    <div className={cn('my-0.5 inline-flex items-center gap-0.5 rounded border px-1.5 py-px text-[10px] font-medium leading-3', tone)}>
      {icon ?? <Icon className={cn('size-2.5', running && 'animate-spin')} />}
      <span className="min-w-0 truncate">{label}</span>
      {argsStreaming && (
        <span className="ml-0.5 inline-flex items-center gap-0.5 rounded bg-cyan-100/70 px-0.5 py-0 text-[9px] text-cyan-700">
          <span className="size-0.5 animate-pulse rounded-full bg-cyan-500" />
          识别中
        </span>
      )}
    </div>
  );
};
