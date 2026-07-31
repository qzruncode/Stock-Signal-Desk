import React, { useState } from 'react';
import { ChevronDown, SlidersHorizontal } from 'lucide-react';
import type { RssFeedOptions } from '../../api/rss';
import { Input } from '../common';
import { cn } from '../../utils/cn';

export interface RssOptionsPanelProps {
  options: RssFeedOptions;
  onOptionsChange: (options: RssFeedOptions) => void;
}

export const RssOptionsPanel: React.FC<RssOptionsPanelProps> = ({ options, onOptionsChange }) => {
  const [open, setOpen] = useState(false);

  const update = <K extends keyof RssFeedOptions>(key: K, value: RssFeedOptions[K]) => {
    onOptionsChange({ ...options, [key]: value });
  };

  const updateStr = (key: keyof RssFeedOptions, value: string) => {
    onOptionsChange({ ...options, [key]: value || undefined });
  };

  const activeCount = [
    options.limit,
    options.filter,
    options.filter_title,
    options.filterout,
    options.opencc,
    options.sorted,
  ].filter((v) => v !== undefined && v !== '' && v !== null).length;

  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-left"
      >
        <span className="flex items-center gap-2 text-xs font-medium text-secondary-text">
          <SlidersHorizontal className="h-3.5 w-3.5" />
          RSSHub 通用选项
          {activeCount > 0 && (
            <span className="rounded-full bg-cyan/15 px-1.5 py-0.5 text-[10px] text-cyan">
              {activeCount}
            </span>
          )}
        </span>
        <ChevronDown className={cn('h-4 w-4 text-muted-text transition', open && 'rotate-180')} />
      </button>

      {open && (
        <div className="grid grid-cols-1 gap-2 border-t border-border px-3 py-3 sm:grid-cols-2">
          <Input
            label="limit（条数）"
            type="number"
            min={1}
            max={100}
            placeholder="如 30"
            value={options.limit?.toString() ?? ''}
            onChange={(e) => {
              const n = e.target.value ? Number(e.target.value) : undefined;
              update('limit', n && n > 0 ? n : undefined);
            }}
          />
          <Input
            label="filter（正则过滤）"
            placeholder="标题/描述/作者/分类"
            value={options.filter ?? ''}
            onChange={(e) => updateStr('filter', e.target.value)}
          />
          <Input
            label="filter_title（标题过滤）"
            placeholder="正则"
            value={options.filter_title ?? ''}
            onChange={(e) => updateStr('filter_title', e.target.value)}
          />
          <Input
            label="filterout（排除）"
            placeholder="正则"
            value={options.filterout ?? ''}
            onChange={(e) => updateStr('filterout', e.target.value)}
          />
          <Input
            label="opencc（繁简转换）"
            placeholder="如 t2s / s2t"
            value={options.opencc ?? ''}
            onChange={(e) => updateStr('opencc', e.target.value)}
          />
          <label className="flex items-center gap-2 text-xs text-secondary-text sm:col-span-2">
            <input
              type="checkbox"
              checked={options.sorted === false}
              onChange={(e) => update('sorted', e.target.checked ? false : undefined)}
              className="h-3.5 w-3.5"
            />
            关闭按时间倒序（sorted=false）
          </label>
        </div>
      )}
    </div>
  );
};

export default RssOptionsPanel;
