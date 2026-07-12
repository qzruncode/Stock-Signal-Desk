import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { rssApi, type GelonghuiSubject } from '../../api/rss';
import { Select } from '../common';

export interface GelonghuiSubjectPickerProps {
  /** Current subject id (the value actually submitted for the route param). */
  value: string;
  /** Called when the user picks a subject. Receives the subjectId as a string. */
  onSelect: (id: string) => void;
}

/**
 * Picker for gelonghui subject ids. The route param `id` has no static option
 * list in RSSHub metadata, but gelonghui exposes its hot subjects via
 * `/api/subjects` (proxied + cached by our backend). We list those here so the
 * user can pick a subject by name instead of guessing a numeric id.
 *
 * Note: the upstream API only exposes ~15 hot subjects (no full list, no
 * search) despite a misleading `totalCount`. A manual-entry fallback covers any
 * subject not in the hot list.
 */
export const GelonghuiSubjectPicker: React.FC<GelonghuiSubjectPickerProps> = ({
  value,
  onSelect,
}) => {
  const [subjects, setSubjects] = useState<GelonghuiSubject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [manual, setManual] = useState(false);

  useEffect(() => {
    let active = true;
    void rssApi
      .getGelonghuiSubjects({})
      .then((res) => {
        if (!active) return;
        setSubjects(res.subjects || []);
        if (res._error && !(res.subjects || []).length) setError(res._error);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError((err as Error)?.message || '主题列表加载失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  if (loading) {
    return (
      <div className="flex h-11 items-center gap-2 rounded-xl border border-border bg-card px-4 text-sm text-muted-text">
        <Loader2 className="h-4 w-4 animate-spin text-cyan" />
        正在加载主题列表…
      </div>
    );
  }

  // Manual-entry fallback: remote list unavailable, or the user wants a subject
  // outside the hot list.
  if (manual || (error && subjects.length === 0)) {
    return (
      <div className="flex flex-col">
        <label className="mb-2 text-sm font-medium text-foreground">id（手动输入）</label>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={value}
            onChange={(e) => onSelect(e.target.value)}
            placeholder="输入主题编号，如 4"
            className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-4 py-2.5 text-sm text-foreground transition-all duration-200 focus:outline-none"
          />
          {subjects.length > 0 && (
            <button
              type="button"
              onClick={() => setManual(false)}
              className="shrink-0 text-[11px] text-cyan hover:underline"
            >
              改为选择
            </button>
          )}
        </div>
        {error && <p className="mt-1 text-[11px] text-danger">{error}（可手动输入 id）</p>}
      </div>
    );
  }

  const options = subjects.map((s) => ({
    value: String(s.subjectId),
    label: `${s.name}（${s.followCount.toLocaleString()} 关注 · #${s.subjectId}）`,
  }));

  return (
    <div className="flex flex-col">
      <div className="mb-2 flex items-center justify-between">
        <label className="text-sm font-medium text-foreground">id（主题）</label>
        <button type="button" onClick={() => setManual(true)} className="text-[11px] text-cyan hover:underline">
          手动输入 id
        </button>
      </div>
      <Select
        value={value}
        onChange={onSelect}
        options={options}
        placeholder={subjects.length ? '选择主题' : '无可用主题，可手动输入 id'}
      />
      {subjects.length === 0 && !error && (
        <p className="mt-1 text-[11px] text-muted-text">未获取到主题列表，可点「手动输入 id」。</p>
      )}
      <p className="mt-1 text-[11px] text-muted-text">
        仅列出热门主题；完整列表见 gelonghui.com/subject 页面 URL 中的编号。
      </p>
    </div>
  );
};

export default GelonghuiSubjectPicker;
