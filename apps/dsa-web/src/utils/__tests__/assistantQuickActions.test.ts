import { describe, expect, it } from 'vitest';

import {
  ASSISTANT_SUGGESTION_GROUPS,
  ASSISTANT_SUGGESTIONS,
} from '../assistantQuickActions';

describe('generic assistant intent examples', () => {
  it('contains three unique intent groups with directly usable prompts', () => {
    const items = ASSISTANT_SUGGESTION_GROUPS.flatMap((group) => group.items);

    expect(ASSISTANT_SUGGESTION_GROUPS.map((group) => group.id)).toEqual([
      'general',
      'research',
      'materials',
    ]);
    expect(new Set(items.map((item) => item.label)).size).toBe(items.length);
    expect(items.every((item) => item.prompt.trim().length >= 12)).toBe(true);
    expect(ASSISTANT_SUGGESTIONS).toEqual(items);
  });

  it('does not encode a tool mapping, capability enum or workflow route', () => {
    const items = ASSISTANT_SUGGESTION_GROUPS.flatMap((group) => group.items);

    for (const item of items) {
      expect(item).not.toHaveProperty('toolName');
      expect(item).not.toHaveProperty('capability');
      expect(item).not.toHaveProperty('workflow');
    }
  });

  it('teaches long-tail handling and evidence discipline instead of an SOP', () => {
    const prompts = ASSISTANT_SUGGESTIONS.map((item) => item.prompt).join('\n');

    expect(prompts).toContain('不常见的问题');
    expect(prompts).toContain('不要套固定模板');
    expect(prompts).toContain('实体、时间口径、来源和证据缺口');
    expect(prompts).toContain('一致、冲突和仍无法确认');
  });

  it('contains no removed composite Agent tool names', () => {
    const serialized = JSON.stringify(ASSISTANT_SUGGESTION_GROUPS);
    for (const removed of [
      'run_stock_analysis',
      'run_batch_analysis',
      'filter_watchlist_by_theme',
      'evaluate_multi_stock_buy_criteria',
      'get_market_regime',
    ]) {
      expect(serialized).not.toContain(removed);
    }
  });
});
