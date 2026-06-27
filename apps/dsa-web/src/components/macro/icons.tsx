import { ArrowDown, ArrowUp, Minus } from 'lucide-react';

export function ArrowIcon({ value }: { value: number | null | undefined }) {
  if (value == null) return <Minus className="h-4 w-4 text-slate-400" />;
  return value >= 0 ? (
    <ArrowUp className="h-4 w-4 text-red-600" />
  ) : (
    <ArrowDown className="h-4 w-4 text-green-600" />
  );
}

export function TrendIcon({ trend }: { trend: string }) {
  if (trend === '上升') return <ArrowUp className="h-4 w-4 text-red-600" />;
  if (trend === '下降') return <ArrowDown className="h-4 w-4 text-green-600" />;
  return <Minus className="h-4 w-4 text-slate-400" />;
}