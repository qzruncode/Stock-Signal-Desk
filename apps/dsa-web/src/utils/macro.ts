// Shared formatters & helpers for macro data display

export function formatNumber(value: number | null | undefined, decimals = 2): string {
  if (value == null) return '-';
  return value.toFixed(decimals);
}

export function formatAmount(value: number | null | undefined): string {
  if (value == null) return '-';
  const abs = Math.abs(value);
  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${(value / 1e4).toFixed(2)}万`;
  return value.toFixed(0);
}

export function formatPct(value: number | null | undefined): string {
  if (value == null) return '-';
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

export function pctColor(value: number | null | undefined): string {
  if (value == null) return 'text-slate-500';
  return value >= 0 ? 'text-red-600' : 'text-green-600';
}
