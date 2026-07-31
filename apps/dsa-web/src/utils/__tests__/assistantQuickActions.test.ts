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
  RSS_DOCUMENT_SUGGESTIONS,
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
    const compatibilityOnlyTools = new Set([
      'list_financial_sources',
      'inspect_financial_source',
      'read_financial_feed',
      'read_financial_article',
      'export_financial_feed',
    ]);
    const plannerFacingTools = backendTools.filter(
      (toolName) => !compatibilityOnlyTools.has(toolName),
    );
    const catalogTools = ASSISTANT_CAPABILITY_GROUPS.flatMap((group) =>
      group.items.flatMap((item) => (item.toolName ? [item.toolName] : [])),
    );

    expect(backendTools).toHaveLength(72);
    expect(plannerFacingTools).toHaveLength(67);
    expect(new Set(catalogTools)).toEqual(new Set(plannerFacingTools));
    expect(catalogTools).toHaveLength(plannerFacingTools.length);
    expect(catalogTools.some((toolName) => compatibilityOnlyTools.has(toolName))).toBe(false);
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
    expect(ASSISTANT_SUGGESTION_GROUPS).toHaveLength(10);
    expect(ASSISTANT_SUGGESTION_GROUPS[0]?.items).toEqual(SUGGESTIONS);
    expect(ASSISTANT_SUGGESTION_GROUPS[1]).toMatchObject({
      id: 'rss-documents',
      items: RSS_DOCUMENT_SUGGESTIONS,
    });
    expect(ASSISTANT_SUGGESTIONS).toHaveLength(
      SUGGESTIONS.length + RSS_DOCUMENT_SUGGESTIONS.length + ASSISTANT_CAPABILITY_COUNT,
    );
    expect(ASSISTANT_SUGGESTIONS.slice(0, SUGGESTIONS.length)).toEqual(SUGGESTIONS);
    expect(
      ASSISTANT_SUGGESTIONS.slice(
        SUGGESTIONS.length,
        SUGGESTIONS.length + RSS_DOCUMENT_SUGGESTIONS.length,
      ),
    ).toEqual(RSS_DOCUMENT_SUGGESTIONS);
    expect(ASSISTANT_SUGGESTIONS.slice(SUGGESTIONS.length + RSS_DOCUMENT_SUGGESTIONS.length)).toEqual(
      ASSISTANT_CAPABILITY_GROUPS.flatMap((group) => group.items),
    );
  });

  it('exposes the RSSHub and original-document flows as concrete home examples', () => {
    expect(RSS_DOCUMENT_SUGGESTIONS).toHaveLength(4);
    expect(RSS_DOCUMENT_SUGGESTIONS.some((item) => item.prompt.includes('健康状态'))).toBe(true);
    expect(RSS_DOCUMENT_SUGGESTIONS.some((item) => item.prompt.includes('PDF 原文件'))).toBe(true);
    expect(RSS_DOCUMENT_SUGGESTIONS.some((item) => item.prompt.includes('逐项引用页码'))).toBe(true);
    expect(RSS_DOCUMENT_SUGGESTIONS.some((item) => item.prompt.includes('JSON Feed'))).toBe(true);
  });
});
