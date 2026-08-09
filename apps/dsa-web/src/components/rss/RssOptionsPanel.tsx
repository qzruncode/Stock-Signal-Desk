import React, { useEffect, useMemo, useState } from 'react';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { ChevronDown, SlidersHorizontal } from 'lucide-react';
import type { RssFeedOptions } from '../../api/rss';
import { Input } from '../common';
import { cn } from '../../utils/cn';

export interface RssOptionsPanelProps {
  options: RssFeedOptions;
  onOptionsChange: (options: RssFeedOptions) => void;
}

interface RssOptionsFormValues {
  limit?: number;
  filter: string;
  filter_title: string;
  filterout: string;
  opencc: string;
  sortedDisabled: boolean;
}

export const RssOptionsPanel: React.FC<RssOptionsPanelProps> = ({ options, onOptionsChange }) => {
  const [open, setOpen] = useState(false);
  const { control, reset } = useForm<RssOptionsFormValues>({
    defaultValues: {
      limit: options.limit,
      filter: options.filter ?? '',
      filter_title: options.filter_title ?? '',
      filterout: options.filterout ?? '',
      opencc: options.opencc ?? '',
      sortedDisabled: options.sorted === false,
    },
  });
  const values = useWatch({ control });

  useEffect(() => {
    reset({
      limit: options.limit,
      filter: options.filter ?? '',
      filter_title: options.filter_title ?? '',
      filterout: options.filterout ?? '',
      opencc: options.opencc ?? '',
      sortedDisabled: options.sorted === false,
    });
  }, [options, reset]);

  const normalizedOptions = useMemo<RssFeedOptions>(() => ({
    limit: values.limit,
    filter: values.filter || undefined,
    filter_title: values.filter_title || undefined,
    filterout: values.filterout || undefined,
    opencc: values.opencc || undefined,
    sorted: values.sortedDisabled ? false : undefined,
  }), [values]);

  useEffect(() => {
    const same = normalizedOptions.limit === options.limit
      && normalizedOptions.filter === options.filter
      && normalizedOptions.filter_title === options.filter_title
      && normalizedOptions.filterout === options.filterout
      && normalizedOptions.opencc === options.opencc
      && normalizedOptions.sorted === options.sorted;
    if (!same) onOptionsChange(normalizedOptions);
  }, [normalizedOptions, onOptionsChange, options]);

  const activeCount = [
    normalizedOptions.limit,
    normalizedOptions.filter,
    normalizedOptions.filter_title,
    normalizedOptions.filterout,
    normalizedOptions.opencc,
    normalizedOptions.sorted,
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
          <Controller
            name="limit"
            control={control}
            render={({ field }) => (
              <Input
                label="limit（条数）"
                type="number"
                min={1}
                max={100}
                placeholder="如 30"
                value={field.value?.toString() ?? ''}
                onChange={(e) => {
                  const n = e.target.value ? Number(e.target.value) : undefined;
                  field.onChange(n && n > 0 ? n : undefined);
                }}
              />
            )}
          />
          {(['filter', 'filter_title', 'filterout', 'opencc'] as const).map((name) => {
            const metadata = {
              filter: ['filter（正则过滤）', '标题/描述/作者/分类'],
              filter_title: ['filter_title（标题过滤）', '正则'],
              filterout: ['filterout（排除）', '正则'],
              opencc: ['opencc（繁简转换）', '如 t2s / s2t'],
            }[name];
            return (
              <Controller
                key={name}
                name={name}
                control={control}
                render={({ field }) => (
                  <Input
                    label={metadata[0]}
                    placeholder={metadata[1]}
                    value={field.value}
                    onChange={field.onChange}
                  />
                )}
              />
            );
          })}
          <Controller
            name="sortedDisabled"
            control={control}
            render={({ field }) => (
              <label className="flex items-center gap-2 text-xs text-secondary-text sm:col-span-2">
                <input
                  type="checkbox"
                  checked={field.value}
                  onChange={(e) => field.onChange(e.target.checked)}
                  className="h-3.5 w-3.5"
                />
                关闭按时间倒序（sorted=false）
              </label>
            )}
          />
        </div>
      )}
    </div>
  );
};

export default RssOptionsPanel;
