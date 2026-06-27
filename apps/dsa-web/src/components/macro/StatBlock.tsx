import { cn } from '../../utils/cn';

export function StatBlock({ label, value, valueClassName }: { label: string; value: string; valueClassName?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-slate-400">{label}</span>
      <span className={cn('tabular-nums text-sm text-slate-700', valueClassName)}>{value}</span>
    </div>
  );
}