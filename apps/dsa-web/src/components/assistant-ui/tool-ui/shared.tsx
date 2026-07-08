import type { FC, ReactNode } from 'react';
import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import { CheckCircle2Icon, Loader2Icon, XCircleIcon } from 'lucide-react';
import { cn } from '../../../utils/cn';

/**
 * 工具调用的运行态药丸(loading/complete/error)。
 * 各内联工具 UI 在 result 尚未返回时统一用它表示进度。
 */
export const ToolStatusPill: FC<{
  status: ToolCallMessagePartProps['status'];
  isError?: boolean;
  label: string;
  icon?: ReactNode;
}> = ({ status, isError, label, icon }) => {
  const failed = isError || (status.type === 'incomplete' && status.reason === 'error');
  const running = status.type === 'running';
  const Icon = failed ? XCircleIcon : running ? Loader2Icon : CheckCircle2Icon;
  const tone = failed
    ? 'border-red-200 bg-red-50 text-red-700'
    : running
      ? 'border-cyan-200 bg-cyan-50 text-cyan-700'
      : 'border-emerald-200 bg-emerald-50 text-emerald-700';
  return (
    <div className={cn('my-2 inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium', tone)}>
      {icon ?? <Icon className={cn('size-3.5', running && 'animate-spin')} />}
      <span>{label}</span>
    </div>
  );
};
