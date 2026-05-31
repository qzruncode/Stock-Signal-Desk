import type { SectorItem } from '../api/sectors';

export type SectorSortField = 'change_pct' | 'up_count' | 'down_count' | 'net_flow' | 'name';
export type SectorSortDirection = 'asc' | 'desc';

export function formatPct(value: number | null | undefined): string {
  if (value == null) return '-';
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

export function formatFlow(value: number | null | undefined): string {
  if (value == null) return '-';
  const abs = Math.abs(value);
  if (abs >= 1e4) return `${(value / 1e4).toFixed(2)}亿`;
  if (abs >= 1e2) return `${(value / 1e2).toFixed(0)}亿`;
  return `${value.toFixed(2)}亿`;
}

export function sortSectorItems(
  items: SectorItem[],
  sortField: SectorSortField,
  sortDir: SectorSortDirection,
): SectorItem[] {
  return [...items].sort((a, b) => {
    if (sortField === 'name') {
      return sortDir === 'asc'
        ? (a.name || '').localeCompare(b.name || '', 'zh')
        : (b.name || '').localeCompare(a.name || '', 'zh');
    }
    const va = (a[sortField] as number | null | undefined) ?? 0;
    const vb = (b[sortField] as number | null | undefined) ?? 0;
    return sortDir === 'asc' ? va - vb : vb - va;
  });
}

export function changeTextColor(value: number | null | undefined, strong = false): string {
  if ((value ?? 0) > 0) return strong ? 'text-red-600' : 'text-red-500';
  if ((value ?? 0) < 0) return strong ? 'text-green-600' : 'text-green-500';
  return 'text-slate-400';
}
