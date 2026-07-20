import { describe, expect, it } from 'vitest';

import {
  ASSISTANT_CAPABILITY_COUNT,
  ASSISTANT_CAPABILITY_GROUPS,
  ASSISTANT_SUGGESTION_GROUPS,
  ASSISTANT_SUGGESTIONS,
  SUGGESTIONS,
} from '../assistantQuickActions';

describe('assistant capability catalog', () => {
  it('contains unique categories, labels and backend tool mappings', () => {
    const items = ASSISTANT_CAPABILITY_GROUPS.flatMap((group) => group.items);
    const toolNames = items.flatMap((item) => (item.toolName ? [item.toolName] : []));

    expect(ASSISTANT_CAPABILITY_GROUPS).toHaveLength(8);
    expect(ASSISTANT_CAPABILITY_COUNT).toBe(66);
    expect(new Set(ASSISTANT_CAPABILITY_GROUPS.map((group) => group.id)).size).toBe(8);
    expect(new Set(items.map((item) => item.label)).size).toBe(items.length);
    expect(new Set(toolNames).size).toBe(toolNames.length);
    expect(toolNames).toHaveLength(61);
    expect(items.every((item) => item.prompt.trim().length > 0)).toBe(true);
  });

  it('keeps the original questions first and appends every capability', () => {
    expect(ASSISTANT_SUGGESTION_GROUPS).toHaveLength(9);
    expect(ASSISTANT_SUGGESTION_GROUPS[0]?.items).toEqual(SUGGESTIONS);
    expect(ASSISTANT_SUGGESTIONS).toHaveLength(71);
    expect(ASSISTANT_SUGGESTIONS.slice(0, SUGGESTIONS.length)).toEqual(SUGGESTIONS);
    expect(ASSISTANT_SUGGESTIONS.slice(SUGGESTIONS.length)).toEqual(
      ASSISTANT_CAPABILITY_GROUPS.flatMap((group) => group.items),
    );
  });
});
