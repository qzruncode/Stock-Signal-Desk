import { describe, expect, it } from 'vitest';
import { describeRssEmptyResult } from '../toolResults';

describe('describeRssEmptyResult', () => {
  it('distinguishes an empty semantic match from an empty data source', () => {
    expect(describeRssEmptyResult({
      success: true,
      days: 30,
      rss_routes: [{ item_count: 18, recent_item_count: 12, relevant_item_count: 0, success: true }],
    }, false)).toBe('数据源返回 18 条，但时间窗内没有主题匹配');
  });

  it('reports time-window filtering separately', () => {
    expect(describeRssEmptyResult({
      success: true,
      days: 7,
      rss_routes: [{ item_count: 9, recent_item_count: 0, relevant_item_count: 0, success: true }],
    }, false)).toBe('数据源返回 9 条，但均不在最近 7 天内');
  });

  it('reports a connected research source and an exhausted web fallback', () => {
    expect(describeRssEmptyResult({
      success: true,
      fallback_attempted: true,
      fallback_used: false,
      source_coverage: [{ item_count: 0, success: true }],
    }, true)).toBe('数据源连接正常，本次查询没有匹配记录；联网兜底也未找到结果');
  });
});
