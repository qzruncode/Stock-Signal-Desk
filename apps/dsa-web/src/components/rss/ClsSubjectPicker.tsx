import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { rssApi, type ClsSubject } from '../../api/rss';
import { Select } from '../common';

export interface ClsSubjectPickerProps {
  /** Current subject id (the value actually submitted for the route param). */
  value: string;
  /** Called when the user picks a subject. Receives the subjectId as a string. */
  onSelect: (id: string) => void;
}

/**
 * Picker for the ``/cls/subject/:id?`` route's optional ``id`` param.
 *
 * RSSHub metadata only names two example ids (``1103`` 盘面直播、``1151`` 有声早报)
 * and tells the user to "在对应话题页 URL 中找到" the id — no option list. cls has
 * no public subject index (the ``/subject`` page is client-rendered; its SSP
 * returns empty ``data: {}``), but each article returned by
 * ``/api/subject/{id}/article`` carries a ``subjects`` array. Our backend seeds
 * from the two default subjects, harvests + dedups the subjects mentioned across
 * their articles, and serves the list attention-desc (proxied + 6h cached).
 *
 * The list only covers hot subjects, not the full set — a manual-entry fallback
 * covers any subject not harvested. ``id`` is optional: an empty value lets
 * RSSHub fall back to its default (盘面直播 ``1103``), so the picker offers a
 * "默认" option alongside the harvested subjects.
 */
export const ClsSubjectPicker: React.FC<ClsSubjectPickerProps> = ({
  value,
  onSelect,
}) => {
  const [subjects, setSubjects] = useState<ClsSubject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [manual, setManual] = useState(false);

  useEffect(() => {
    let active = true;
    void rssApi
      .getClsSubjects({})
      .then((res) => {
        if (!active) return;
        setSubjects(res.subjects || []);
        if (res._error && !(res.subjects || []).length) setError(res._error);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError((err as Error)?.message || '话题列表加载失败');
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
        正在加载话题列表…
      </div>
    );
  }

  // Manual-entry fallback: remote list unavailable, or the user wants a subject
  // outside the harvested hot list.
  if (manual || (error && subjects.length === 0)) {
    return (
      <div className="flex flex-col">
        <label className="mb-2 text-sm font-medium text-foreground">id（手动输入）</label>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={value}
            onChange={(e) => onSelect(e.target.value)}
            placeholder="输入话题编号，如 1103"
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

  // id is optional — '' lets RSSHub use its default (盘面直播 1103).
  const options = [
    { value: '', label: '默认（盘面直播）' },
    ...subjects.map((s) => ({
      value: String(s.subjectId),
      label: `${s.name}（${s.attention_num.toLocaleString()} 关注 · #${s.subjectId}）`,
    })),
  ];

  return (
    <div className="flex flex-col">
      <div className="mb-2 flex items-center justify-between">
        <label className="text-sm font-medium text-foreground">id（话题）</label>
        <button type="button" onClick={() => setManual(true)} className="text-[11px] text-cyan hover:underline">
          手动输入 id
        </button>
      </div>
      <Select
        value={value}
        onChange={onSelect}
        options={options}
        placeholder={subjects.length ? '选择话题' : '无可用话题，可手动输入 id'}
      />
      {subjects.length === 0 && !error && (
        <p className="mt-1 text-[11px] text-muted-text">未获取到话题列表，可点「手动输入 id」。</p>
      )}
      <p className="mt-1 text-[11px] text-muted-text">
        仅列出热门话题（由默认话题文章收割）；留空使用默认盘面直播。
      </p>
    </div>
  );
};

export default ClsSubjectPicker;
