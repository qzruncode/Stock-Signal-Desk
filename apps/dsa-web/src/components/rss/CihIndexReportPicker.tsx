import React, { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { rssApi, type CihIndexCategory } from '../../api/rss';
import { Select } from '../common';

export interface CihIndexReportPickerProps {
  /** Current `report` path segment (e.g. `f<id>-p1-oaddtime-ddesc` or `p1-oaddtime-ddesc`). */
  value: string;
  /** Called with the fully-assembled `report` path segment when the user picks a category. */
  onSelect: (segment: string) => void;
}

/**
 * Picker for the ``/cih-index/report/list/:report?`` route's ``report`` param.
 *
 * ``report`` is not a single id but a **composite path segment**: upstream
 * encodes category/sort/page as ``{prefix}{value}`` fragments joined by ``-``
 * (f=一级分类, s=二级, p=页码, t=标签, o=排序字段, d=排序方向). The default
 * ``p1-oaddtime-ddesc`` lists all reports by add-time desc.
 *
 * RSSHub metadata only says "可在 URL 中找到" with no option list, but the
 * report list page's ``__INITIAL_STATE__.indNavLists`` embeds the 8 top-level
 * categories (proxied + 6h-cached by our backend). We list those here so the
 * user picks by name; on select we splice the chosen ``f<classId>`` into a
 * standard ``f<id>-p1-oaddtime-ddesc`` segment (or drop it for "全部").
 *
 * Only the top-level category is exposed — second-level/tag/sort rarely matter
 * for a feed and their encodings need a chosen first-level to enumerate, so we
 * keep the picker simple. Power users can still hand-edit the segment.
 */
export const CihIndexReportPicker: React.FC<CihIndexReportPickerProps> = ({
  value,
  onSelect,
}) => {
  const [categories, setCategories] = useState<CihIndexCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [manual, setManual] = useState(false);

  useEffect(() => {
    let active = true;
    void rssApi
      .getCihIndexCategories({})
      .then((res) => {
        if (!active) return;
        setCategories(res.categories || []);
        if (res._error && !(res.categories || []).length) setError(res._error);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError((err as Error)?.message || '分类列表加载失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  // Reverse-parse the current `report` segment to find the selected first-level
  // classId (the `f`-prefixed fragment). Falls back to '' (全部) when absent.
  const selectedClassId = useMemo(() => {
    if (!value) return '';
    const frag = value
      .split('-')
      .find((seg) => seg.startsWith('f') && seg.length > 1);
    return frag ? frag.slice(1) : '';
  }, [value]);

  const buildSegment = (classId: string): string => {
    // Keep the shape stable: f<id>-p1-oaddtime-ddesc (with category) or
    // p1-oaddtime-ddesc (all). Fragment order is irrelevant upstream — it
    // matches by prefix — but we follow the documented order for readability.
    return classId ? `f${classId}-p1-oaddtime-ddesc` : 'p1-oaddtime-ddesc';
  };

  if (loading) {
    return (
      <div className="flex h-11 items-center gap-2 rounded-xl border border-border bg-card px-4 text-sm text-muted-text">
        <Loader2 className="h-4 w-4 animate-spin text-cyan" />
        正在加载分类列表…
      </div>
    );
  }

  // Manual-entry fallback: remote list unavailable, or the user wants a custom
  // segment (second-level/tag/sort) the picker doesn't expose.
  if (manual || (error && categories.length === 0)) {
    return (
      <div className="flex flex-col">
        <label className="mb-2 text-sm font-medium text-foreground">report（手动输入）</label>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={value}
            onChange={(e) => onSelect(e.target.value)}
            placeholder="如 f<classId>-p1-oaddtime-ddesc"
            className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-4 py-2.5 text-sm text-foreground transition-all duration-200 focus:outline-none"
          />
          {categories.length > 0 && (
            <button
              type="button"
              onClick={() => setManual(false)}
              className="shrink-0 text-[11px] text-cyan hover:underline"
            >
              改为选择
            </button>
          )}
        </div>
        <p className="mt-1 text-[11px] leading-relaxed text-muted-text">
          路径段格式：<code>f</code>一级分类 · <code>s</code>二级 · <code>p</code>页码 · <code>t</code>标签 · <code>o</code>排序字段 · <code>d</code>方向，用 - 连接，留空为 <code>p1-oaddtime-ddesc</code>。
        </p>
        {error && <p className="mt-1 text-[11px] text-danger">{error}（可手动输入）</p>}
      </div>
    );
  }

  const options = [
    { value: '', label: '全部报告' },
    ...categories.map((c) => ({ value: c.classId, label: c.className })),
  ];

  return (
    <div className="flex flex-col">
      <div className="mb-2 flex items-center justify-between">
        <label className="text-sm font-medium text-foreground">report（一级分类）</label>
        <button type="button" onClick={() => setManual(true)} className="text-[11px] text-cyan hover:underline">
          手动输入路径段
        </button>
      </div>
      <Select
        value={selectedClassId}
        onChange={(classId) => onSelect(buildSegment(classId))}
        options={options}
        placeholder={categories.length ? '选择分类' : '无可用分类，可手动输入'}
      />
      {categories.length === 0 && !error && (
        <p className="mt-1 text-[11px] text-muted-text">未获取到分类列表，可点「手动输入路径段」。</p>
      )}
      <p className="mt-1 text-[11px] leading-relaxed text-muted-text">
        选分类后自动拼路径段（当前：<code className="break-all">{value || 'p1-oaddtime-ddesc'}</code>）。需二级分类/标签/排序可手动输入。
      </p>
    </div>
  );
};

export default CihIndexReportPicker;
