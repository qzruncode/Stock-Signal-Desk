/**
 * Stock Search Algorithm
 *
 * Supports multiple matching methods:
 * - Exact match: code, name, pinyin, alias
 * - Prefix match: code prefix, name prefix, pinyin prefix
 * - Contains match: code contains, name contains, pinyin contains
 */

import type { StockIndexItem, StockSuggestion } from '../types/stockIndex';
import { normalizeQuery } from './normalizeQuery';
import { MATCH_SCORE, SEARCH_CONFIG } from './stockIndexFields';

export interface SearchOptions {
  /** Limit on number of results to return */
  limit?: number;
  /** Show only active stocks */
  activeOnly?: boolean;
}

type NormalizedStockFields = {
  canonicalCode: string;
  displayCode: string;
  name: string;
  pinyinFull: string;
  pinyinAbbr: string;
  aliases: string[];
};

// The loaded stock index is immutable during a session. Cache normalized
// fields per object so repeated autocomplete queries do not repeat the same
// Unicode normalization work for every stock.
const normalizedFieldsCache = new WeakMap<StockIndexItem, NormalizedStockFields>();

function getNormalizedStockFields(item: StockIndexItem): NormalizedStockFields {
  const cached = normalizedFieldsCache.get(item);
  if (cached) return cached;

  const normalized: NormalizedStockFields = {
    canonicalCode: normalizeQuery(item.canonicalCode),
    displayCode: normalizeQuery(item.displayCode),
    name: normalizeQuery(item.nameZh),
    pinyinFull: normalizeQuery(item.pinyinFull || ''),
    pinyinAbbr: normalizeQuery(item.pinyinAbbr || ''),
    aliases: item.aliases?.map(alias => normalizeQuery(alias)) || [],
  };
  normalizedFieldsCache.set(item, normalized);
  return normalized;
}

/**
 * Search stock index
 *
 * @param query - Search query
 * @param index - Stock index
 * @param options - Search options
 * @returns List of matched stock suggestions
 */
export function searchStocks(
  query: string,
  index: StockIndexItem[],
  options: SearchOptions = {}
): StockSuggestion[] {
  const normalizedQuery = normalizeQuery(query);
  if (!normalizedQuery) {
    return [];
  }
  const limit = options.limit ?? SEARCH_CONFIG.DEFAULT_LIMIT;
  const activeOnly = options.activeOnly !== false;

  // Filter index
  const filteredIndex = index.filter(item => {
    if (activeOnly && !item.active) return false;
    return true;
  });

  // Calculate match score for each item
  const suggestions = filteredIndex.map(item => ({
    item,
    score: calculateMatchScore(normalizedQuery, item),
  }));

  // Filter out items with score of 0
  const matched = suggestions.filter(s => s.score > 0);

  // Sort: by score descending, then by popularity descending for same score
  matched.sort((a, b) => {
    if (a.score !== b.score) return b.score - a.score;
    return (b.item.popularity || 0) - (a.item.popularity || 0);
  });

  // Return top N items
  return matched.slice(0, limit).map(s => ({
    canonicalCode: s.item.canonicalCode,
    displayCode: s.item.displayCode,
    nameZh: s.item.nameZh,
    market: s.item.market,
    matchType: determineMatchType(s.score),
    matchField: determineMatchField(normalizedQuery, s.item),
    score: s.score,
  }));
}

/**
 * Calculate match score
 *
 * Score rules:
 * - 100: Exact match canonical code
 * - 99: Exact match display code
 * - 98: Exact match Chinese name
 * - 97: Exact match alias
 * - 96: Exact match pinyin abbreviation
 * - 80-89: Prefix match
 * - 60-69: Contains match
 * - 0: No match
 */
function calculateMatchScore(query: string, item: StockIndexItem): number {
  let score = 0;
  const q = query;
  const { canonicalCode, displayCode, name, pinyinFull, pinyinAbbr, aliases } = getNormalizedStockFields(item);

  // 1. Exact match (96-100 points)
  if (q === canonicalCode) return 100;
  if (q === displayCode) return 99;
  if (q === name) return 98;
  if (aliases.some(a => a === q)) return 97;
  if (q === pinyinAbbr) return 96;

  // 2. Prefix match (77-80 points)
  if (displayCode.startsWith(q)) score = Math.max(score, 80);
  if (name.startsWith(q)) score = Math.max(score, 79);
  if (pinyinAbbr.startsWith(q)) score = Math.max(score, 78);
  if (aliases.some(a => a.startsWith(q))) score = Math.max(score, 77);

  // 3. Contains match (57-60 points)
  if (displayCode.includes(q)) score = Math.max(score, 60);
  if (name.includes(q)) score = Math.max(score, 59);
  if (pinyinFull.includes(q)) score = Math.max(score, 58);
  if (aliases.some(a => a.includes(q))) score = Math.max(score, 57);

  return score;
}

/**
 * Determine match type based on score
 */
function determineMatchType(score: number): 'exact' | 'prefix' | 'contains' | 'fuzzy' {
  if (score >= MATCH_SCORE.EXACT_MIN) return 'exact';
  if (score >= MATCH_SCORE.PREFIX_MIN) return 'prefix';
  if (score >= MATCH_SCORE.CONTAINS_MIN) return 'contains';
  return 'fuzzy';
}

/**
 * Determine match field
 */
function determineMatchField(query: string, item: StockIndexItem): 'code' | 'name' | 'pinyin' | 'alias' {
  const q = query;
  const { canonicalCode, displayCode, name, pinyinFull, pinyinAbbr, aliases } = getNormalizedStockFields(item);

  if (canonicalCode.includes(q) || displayCode.includes(q)) {
    return 'code';
  }
  if (name.includes(q)) return 'name';
  if (pinyinFull.includes(q) || pinyinAbbr.includes(q)) {
    return 'pinyin';
  }
  if (aliases.some(a => a.includes(q))) return 'alias';
  return 'name';
}

/**
 * Escape HTML entities
 */
function escapeHtml(unsafe: string): string {
  return unsafe
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

/**
 * Highlight matched text
 *
 * @param text - Original text
 * @param query - Query string
 * @returns Safe HTML string with highlight markers
 */
export function highlightMatch(text: string, query: string): string {
  const normalizedQuery = normalizeQuery(query);
  if (!normalizedQuery) return escapeHtml(text);

  const index = text.toLowerCase().indexOf(normalizedQuery);
  if (index === -1) return escapeHtml(text);

  const before = text.substring(0, index);
  const match = text.substring(index, index + normalizedQuery.length);
  const after = text.substring(index + normalizedQuery.length);

  // Return escaped segments joined by safe <mark> tags
  return `${escapeHtml(before)}<mark>${escapeHtml(match)}</mark>${escapeHtml(after)}`;
}
