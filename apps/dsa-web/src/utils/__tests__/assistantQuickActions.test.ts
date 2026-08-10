import { describe, expect, it } from 'vitest';

import {
  ASSISTANT_SUGGESTION_GROUPS,
  ASSISTANT_SUGGESTIONS,
} from '../assistantQuickActions';

describe('assistant intent examples', () => {
  it('contains four unique intent groups with directly usable prompts', () => {
    const items = ASSISTANT_SUGGESTION_GROUPS.flatMap((group) => group.items);

    expect(ASSISTANT_SUGGESTION_GROUPS.map((group) => group.id)).toEqual([
      'research',
      'stock-tools',
      'verification',
      'general',
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

  it('teaches research and evidence discipline without encoding an SOP', () => {
    const prompts = ASSISTANT_SUGGESTIONS.map((item) => item.prompt).join('\n');

    expect(prompts).toContain('数据时间');
    expect(prompts).toContain('证据缺口');
    expect(prompts).toContain('一致、冲突');
    expect(prompts).toContain('股票分组');
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
