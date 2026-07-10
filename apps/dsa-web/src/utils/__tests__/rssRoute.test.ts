import { describe, expect, it } from 'vitest';
import {
  paramsFromExample,
  parseMarkdownParamOptions,
  parseRouteParams,
  routeDescriptionProse,
} from '../rssRoute';

describe('rssRoute helpers', () => {
  it('parses required and optional route params', () => {
    expect(parseRouteParams('/jin10/category/:id/:type?')).toEqual([
      { name: 'id', hasRegex: false, optional: false },
      { name: 'type', hasRegex: false, optional: true },
    ]);
  });

  it('prefills params from the RSSHub example', () => {
    expect(paramsFromExample('/jin10/category/:id', '/jin10/category/36')).toEqual({ id: '36' });
  });

  it('turns RSSHub markdown tables into select options', () => {
    const markdown = '| Name | ID |\n| --- | --- |\n| 黄金 | 2 |\n| 外汇 | 12 |';
    expect(parseMarkdownParamOptions(markdown)).toEqual([
      { value: '2', label: '黄金（2）' },
      { value: '12', label: '外汇（12）' },
    ]);
    expect(routeDescriptionProse(markdown)).toBe('');

    const horizontal = '| 要闻 | A 股 | 美股 |\n| --- | --- | --- |\n| global | a-stock | us-stock |';
    expect(parseMarkdownParamOptions(horizontal)).toEqual([
      { value: 'global', label: '要闻（global）' },
      { value: 'a-stock', label: 'A 股（a-stock）' },
      { value: 'us-stock', label: '美股（us-stock）' },
    ]);
  });
});
