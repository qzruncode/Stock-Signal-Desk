import { ArrowDown, ArrowUp, Clock, Minus } from 'lucide-react';
import type { ReactNode } from 'react';
import type { SectorItem, SectorListResponse } from '../../api/sectors';
import { cn } from '../../utils/cn';
import {
  sortSectorItems,
  type SectorSortDirection,
  type SectorSortField,
} from '../../utils/sectorFormat';

export interface SectorColumn {
  field?: SectorSortField;
  label: string;
  align?: 'left' | 'right';
  render: (item: SectorItem) => ReactNode;
}

interface SectorTableProps {
  data: SectorListResponse | null;
  loading: boolean;
  error: string | null;
  emptyMessage: string;
  loadingMessage: string;
  footerLabel: string;
  columns: SectorColumn[];
  sortField: SectorSortField;
  sortDir: SectorSortDirection;
  onSort: (field: SectorSortField) => void;
}

function renderSortIcon(activeField: SectorSortField, sortField: SectorSortField, sortDir: SectorSortDirection) {
  if (sortField !== activeField) return <Minus className="h-3 w-3 text-slate-300" />;
  return sortDir === 'asc'
    ? <ArrowUp className="h-3 w-3 text-cyan-600" />
    : <ArrowDown className="h-3 w-3 text-cyan-600" />;
}

export default function SectorTable({
  data,
  loading,
  error,
  emptyMessage,
  loadingMessage,
  footerLabel,
  columns,
  sortField,
  sortDir,
  onSort,
}: SectorTableProps) {
  if (loading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
          <span className="text-sm text-slate-400">{loadingMessage}</span>
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 p-8 text-center">
        <p className="text-sm font-medium text-red-600">{error}</p>
      </div>
    );
  }

  if (!data?.items?.length) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
        <p className="text-sm text-slate-400">{emptyMessage}</p>
      </div>
    );
  }

  const sorted = sortSectorItems(data.items, sortField, sortDir);

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/88 shadow-sm">
      <div className="max-h-[calc(100vh-18rem)] overflow-x-auto overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10">
            <tr className="border-b border-slate-200 bg-slate-50/70 backdrop-blur-sm">
              {columns.map((column) => {
                const isRight = column.align === 'right';
                const canSort = Boolean(column.field);
                return (
                  <th
                    key={`${column.label}-${column.field || 'static'}`}
                    className={cn(
                      'px-3 py-3 font-medium text-slate-600',
                      isRight ? 'text-right' : 'text-left',
                      canSort && 'cursor-pointer hover:text-slate-900',
                      column === columns[0] && 'px-4',
                    )}
                    onClick={column.field ? () => onSort(column.field!) : undefined}
                  >
                    <span className={cn('inline-flex items-center gap-1', isRight && 'justify-end')}>
                      {column.label}
                      {column.field ? renderSortIcon(column.field, sortField, sortDir) : null}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.map((item, idx) => (
              <tr
                key={item.code || item.name || idx}
                className={cn('border-b border-slate-100 transition-colors hover:bg-slate-50', idx % 2 === 0 && 'bg-white/50')}
              >
                {columns.map((column) => (
                  <td
                    key={`${item.code || item.name || idx}-${column.label}`}
                    className={cn(
                      'px-3 py-2.5',
                      column.align === 'right' && 'text-right',
                      column === columns[0] && 'px-4',
                    )}
                  >
                    {column.render(item)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-1.5 border-t border-slate-100 px-4 py-2.5 text-xs text-slate-400">
        <Clock className="h-3 w-3" />
        <span>
          数据获取时间: {data._fetched_at ? new Date(data._fetched_at).toLocaleString('zh-CN') : '-'}
          {data._cached ? ' · 缓存' : ' · 实时'}
          {' · '}共 {data.items.length} 个{footerLabel}
        </span>
      </div>
    </div>
  );
}
