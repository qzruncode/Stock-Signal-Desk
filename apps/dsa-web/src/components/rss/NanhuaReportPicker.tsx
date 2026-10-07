import React, { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { rssApi, type NanhuaReportType1 } from '../../api/rss';
import { Select } from '../common';

export interface NanhuaReportPickerProps {
  /** Current type1 value (the first route param). */
  value1: string;
  /** Current type2 value (the second route param). */
  value2: string;
  /** Called when either param changes — receives the param name + new value. */
  onSelect: (param: 'type1' | 'type2', value: string) => void;
}

/**
 * Cascade picker for the `/nanhua/report/:type1/:type2` route. The route's params
 * have no static option list in RSSHub metadata, but nanhua's own `getTreeList`
 * endpoint returns the full legal type1→type2 tree (proxied + 6h cached by our
 * backend). type2 must share type1's prefix (e.g. `HOT` → `HOT_black`, NOT
 * `WEEK_black`); picking an out-of-tree combo makes upstream return empty and
 * RSSHub throws `this route is empty` 503. This picker makes illegal combos
 * impossible: choosing a type1 narrows type2 to that category's children and
 * auto-selects the first valid one.
 *
 * Fallback: if the tree fails to load, both fields render as manual text inputs
 * (prefilled from the route example) so the route stays usable.
 */
export const NanhuaReportPicker: React.FC<NanhuaReportPickerProps> = ({
  value1,
  value2,
  onSelect,
}) => {
  const [types, setTypes] = useState<NanhuaReportType1[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void rssApi
      .getNanhuaReportTypes({})
      .then((res) => {
        if (!active) return;
        setTypes(res.types || []);
        if (res._error && !(res.types || []).length) setError(res._error);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError((err as Error)?.message || '分类树加载失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  // type1 options: "名称（code）" so the user picks by readable name.
  const type1Options = useMemo(
    () =>
      types.map((t) => ({
        value: t.type,
        label: t.name ? `${t.name}（${t.type}）` : t.type,
      })),
    [types],
  );

  // type2 options narrow to the selected type1's children.
  const selectedType1 = useMemo(
    () => types.find((t) => t.type === value1),
    [types, value1],
  );
  const type2Options = useMemo(() => {
    const children = selectedType1?.children || [];
    return children.map((c) => ({
      value: c.type,
      label: c.name ? `${c.name}（${c.type}）` : c.type,
    }));
  }, [selectedType1]);

  // When type1 changes (or the tree first loads), ensure type2 is legal for it:
  // keep the current type2 only if it belongs to the new type1; otherwise pick
  // the first child. Runs once per type1 value, not on every keystroke.
  useEffect(() => {
    if (!types.length || !value1) return;
    const children = selectedType1?.children || [];
    if (!children.length) return;
    const stillLegal = children.some((c) => c.type === value2);
    if (!stillLegal) {
      onSelect('type2', children[0].type);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value1, selectedType1]);

  if (loading) {
    return (
      <div className="flex h-11 items-center gap-2 rounded-xl border border-border bg-card px-4 text-sm text-muted-text">
        <Loader2 className="h-4 w-4 animate-spin text-cyan" />
        正在加载南华研报分类…
      </div>
    );
  }

  // Manual-entry fallback: tree unavailable. Keep both params usable.
  if (error && types.length === 0) {
    return (
      <div className="space-y-2">
        <p className="text-[11px] text-danger">{error}（可手动输入分类代码）</p>
        <input
          type="text"
          value={value1}
          onChange={(e) => onSelect('type1', e.target.value)}
          placeholder="type1，如 WEEK"
          className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-4 py-2.5 text-sm text-foreground transition-all duration-200 focus:outline-none"
        />
        <input
          type="text"
          value={value2}
          onChange={(e) => onSelect('type2', e.target.value)}
          placeholder="type2，如 WEEK_black（须与 type1 同前缀）"
          className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-4 py-2.5 text-sm text-foreground transition-all duration-200 focus:outline-none"
        />
        <p className="text-[11px] text-muted-text">
          type2 须与 type1 同前缀（HOT 配 HOT_black，非 WEEK_black），否则上游返回空。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <Select
        label="type1（一级分类）"
        value={value1}
        onChange={(value) => onSelect('type1', value)}
        options={type1Options}
        placeholder={type1Options.length ? '选择一级分类' : '无可用分类，可手动输入'}
      />
      {type2Options.length > 0 ? (
        <Select
          label="type2（二级分类）"
          value={value2}
          onChange={(value) => onSelect('type2', value)}
          options={type2Options}
          placeholder="选择二级分类"
        />
      ) : (
        <p className="text-[11px] text-muted-text">
          该一级分类下暂无二级分类，可手动输入 type2。
          <input
            type="text"
            value={value2}
            onChange={(e) => onSelect('type2', e.target.value)}
            placeholder={`type2，如 ${value1}_black`}
            className="input-surface input-focus-glow mt-1.5 h-11 w-full rounded-xl border bg-transparent px-4 py-2.5 text-sm text-foreground transition-all duration-200 focus:outline-none"
          />
        </p>
      )}
      <p className="text-[11px] text-muted-text">
        type2 随 type1 联动，仅列出该分类下的合法二级分类。
      </p>
    </div>
  );
};
