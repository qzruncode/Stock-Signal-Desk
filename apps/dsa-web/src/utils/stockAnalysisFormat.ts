export function formatMarketCap(value: number | null): string {
  if (value == null) return '-';
  if (value >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (value >= 1e4) return `${(value / 1e4).toFixed(2)}万`;
  return value.toFixed(2);
}

export function formatVolume(value: number | null): string {
  if (value == null) return '-';
  if (value >= 1e8) return `${(value / 1e8).toFixed(2)}亿手`;
  if (value >= 1e4) return `${(value / 1e4).toFixed(2)}万手`;
  return `${value}手`;
}

export function formatAmount(value: number | null): string {
  if (value == null) return '-';
  if (value >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (value >= 1e4) return `${(value / 1e4).toFixed(2)}万`;
  return value.toFixed(2);
}

export function formatShares(value: number | null): string {
  if (value == null) return '-';
  if (value >= 1e8) return `${(value / 1e8).toFixed(2)}亿股`;
  if (value >= 1e4) return `${(value / 1e4).toFixed(2)}万股`;
  return `${value.toFixed(0)}股`;
}

export function formatRatio(value: number | null): string {
  return value == null ? '-' : value.toFixed(2);
}

export function formatPctValue(value: number | null | undefined): string {
  return value == null ? '-' : `${value.toFixed(2)}%`;
}

export function formatSourceChain(sources?: string[]): string {
  return sources?.filter(Boolean).join(' / ') || '-';
}
