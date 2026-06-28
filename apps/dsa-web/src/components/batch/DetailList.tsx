import React from 'react';
import { Search, X } from 'lucide-react';
import { cn } from '../../utils/cn';
import type { BatchResultItem } from '../../api/batch';

interface DetailListProps {
  results: BatchResultItem[];
  filteredResults: BatchResultItem[];
  selectedCode: string | null;
  resultSearch: string;
  onSearchChange: (value: string) => void;
  onSelectCode: (code: string) => void;
}

export const DetailList: React.FC<DetailListProps> = ({
  results,
  filteredResults,
  selectedCode,
  resultSearch,
  onSearchChange,
  onSelectCode,
}) => (
  <aside className="flex min-h-0 flex-col border-b border-subtle lg:border-b-0 lg:border-r">
    <div className="border-b border-subtle px-4 py-3">
      <p className="text-sm font-semibold text-foreground">单股结果</p>
      <p className="text-xs text-muted-text">
        {resultSearch.trim() ? `${filteredResults.length} / ${results.length} 条匹配` : `${results.length} 条已保存结果`}
      </p>
      <div className="relative mt-3">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-text" />
        <input
          value={resultSearch}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="搜索代码、摘要、状态..."
          className="h-9 w-full rounded-lg border border-subtle bg-background pl-9 pr-9 text-sm text-foreground outline-none transition placeholder:text-muted-text/70 focus:border-primary/40 focus:ring-2 focus:ring-primary/10"
        />
        {resultSearch && (
          <button
            type="button"
            onClick={() => onSearchChange('')}
            className="absolute right-1.5 top-1/2 inline-flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-md text-muted-text transition hover:bg-hover hover:text-foreground"
            aria-label="清空搜索"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </div>
    <div className="min-h-0 flex-1 overflow-y-auto">
      {filteredResults.length > 0 ? filteredResults.map((item) => (
        <button
          key={item.code}
          type="button"
          onClick={() => onSelectCode(item.code)}
          className={cn(
            'flex w-full flex-col gap-1 border-b border-subtle px-4 py-3 text-left transition-colors hover:bg-hover/70',
            selectedCode === item.code && 'bg-primary/10',
          )}
        >
          <span className="flex items-center justify-between gap-3">
            <span className="font-mono text-sm font-semibold text-foreground">{item.code}</span>
            <span className={cn(
              'rounded-full px-2 py-0.5 text-[10px]',
              item.success ? 'bg-emerald-500/10 text-emerald-600' : 'bg-red-500/10 text-red-600',
            )}
            >
              {item.success ? '成功' : '失败'}
            </span>
          </span>
          <span className="line-clamp-2 text-xs leading-5 text-muted-text">{item.summary}</span>
        </button>
      )) : (
        <div className="px-4 py-8 text-center text-sm text-muted-text">
          没有匹配的单股结果
        </div>
      )}
    </div>
  </aside>
);