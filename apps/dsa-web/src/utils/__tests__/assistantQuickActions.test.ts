/// <reference types="node" />

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import {
  ASSISTANT_CAPABILITY_COUNT,
  ASSISTANT_CAPABILITY_GROUPS,
  ASSISTANT_SUGGESTION_GROUPS,
  ASSISTANT_SUGGESTIONS,
  ASSISTANT_TOOL_CAPABILITY_COUNT,
  SUGGESTIONS,
} from '../assistantQuickActions';

describe('assistant capability catalog', () => {
  it('contains unique categories, labels and backend tool mappings', () => {
    const items = ASSISTANT_CAPABILITY_GROUPS.flatMap((group) => group.items);
    const toolNames = items.flatMap((item) => (item.toolName ? [item.toolName] : []));
    const generalItemCount =
      ASSISTANT_CAPABILITY_GROUPS.find((group) => group.id === 'general')?.items.length ?? 0;

    expect(ASSISTANT_CAPABILITY_GROUPS).toHaveLength(8);
    expect(ASSISTANT_CAPABILITY_COUNT).toBe(items.length);
    expect(new Set(ASSISTANT_CAPABILITY_GROUPS.map((group) => group.id)).size).toBe(8);
    expect(new Set(items.map((item) => item.label)).size).toBe(items.length);
    expect(new Set(toolNames).size).toBe(toolNames.length);
    expect(toolNames).toHaveLength(items.length - generalItemCount);
    expect(ASSISTANT_TOOL_CAPABILITY_COUNT).toBe(toolNames.length);
    expect(items.every((item) => item.prompt.trim().length > 0)).toBe(true);
  });

  it('covers every registered backend tool with one user-facing prompt example', () => {
    const registrySource = readFileSync(
      resolve(process.cwd(), '../../src/tools/registry.py'),
      'utf8',
    );
    const modulesBlock = registrySource.match(
      /TOOL_MODULES:\s*tuple\[str,\s*\.\.\.\]\s*=\s*\(([\s\S]*?)\n\)/,
    )?.[1];
    expect(modulesBlock).toBeTruthy();

    const backendTools = [
      ...(modulesBlock ?? '').matchAll(/^\s*"([^"]+)",/gm),
    ].map((match) => match[1]);
    const catalogTools = ASSISTANT_CAPABILITY_GROUPS.flatMap((group) =>
      group.items.flatMap((item) => (item.toolName ? [item.toolName] : [])),
    );

    expect(backendTools).toHaveLength(66);
    expect(new Set(catalogTools)).toEqual(new Set(backendTools));
    expect(catalogTools).toHaveLength(backendTools.length);
  });

  it('provides concrete, directly usable prompt examples', () => {
    const items = ASSISTANT_CAPABILITY_GROUPS.flatMap((group) => group.items);

    expect(items.every((item) => item.prompt.length >= 12)).toBe(true);
    expect(
      items.some((item) =>
        item.prompt.includes('成立条件')
        && item.prompt.includes('失效信号')
        && item.prompt.includes('置信度'),
      ),
    ).toBe(true);
  });

  it('keeps the original questions first and appends every capability', () => {
    expect(ASSISTANT_SUGGESTION_GROUPS).toHaveLength(9);
    expect(ASSISTANT_SUGGESTION_GROUPS[0]?.items).toEqual(SUGGESTIONS);
    expect(ASSISTANT_SUGGESTIONS).toHaveLength(
      SUGGESTIONS.length + ASSISTANT_CAPABILITY_COUNT,
    );
    expect(ASSISTANT_SUGGESTIONS.slice(0, SUGGESTIONS.length)).toEqual(SUGGESTIONS);
    expect(ASSISTANT_SUGGESTIONS.slice(SUGGESTIONS.length)).toEqual(
      ASSISTANT_CAPABILITY_GROUPS.flatMap((group) => group.items),
    );
  });
});
