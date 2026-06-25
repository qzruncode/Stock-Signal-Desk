import { cn } from '../../utils/cn';

interface DataItemProps {
  label: string;
  value: string;
  highlight?: boolean;
  highlightUp?: boolean;
  highlightDown?: boolean;
}

export function DataItem({ label, value, highlight, highlightUp, highlightDown }: DataItemProps) {
  const valueColor = highlightUp
    ? 'text-red-600 font-semibold'
    : highlightDown
      ? 'text-green-600 font-semibold'
      : highlight
        ? 'text-slate-900 font-semibold'
        : 'text-slate-700';
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-slate-400">{label}</span>
      <span className={cn('tabular-nums text-sm', valueColor)}>{value}</span>
    </div>
  );
}
