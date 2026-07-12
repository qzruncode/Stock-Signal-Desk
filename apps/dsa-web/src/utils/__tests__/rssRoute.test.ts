import { describe, expect, it } from 'vitest';
import {
  isEnglishOnly,
  isHiddenFromExplore,
  isKnownBroken,
  isUnuseful,
  paramsFromExample,
  parseDefaultFromDescription,
  parseMarkdownParamOptions,
  parseRouteParams,
  routeDescriptionProse,
  routeNeedsRemoteOptions,
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

  it('turns a single-row header table into value-as-label options', () => {
    // RSSHub writes a bare value list as a one-row header + separator (no data
    // row), e.g. 10jqka realtimenews tag / 10jqka video category.
    const singleRow = '| 全部 | 重要 | A 股 | 港股 | 美股 | 机会 | 异动 | 公告 |\n| ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |';
    expect(parseMarkdownParamOptions(singleRow)).toEqual([
      { value: '全部', label: '全部' },
      { value: '重要', label: '重要' },
      { value: 'A 股', label: 'A 股' },
      { value: '港股', label: '港股' },
      { value: '美股', label: '美股' },
      { value: '机会', label: '机会' },
      { value: '异动', label: '异动' },
      { value: '公告', label: '公告' },
    ]);
  });

  it('strips ::: containers and tables from the prose description', () => {
    const desc = [
      '::: tip',
      '若订阅 [7×24 小时要闻直播](https://news.10jqka.com.cn/realtimenews.html) 的 `公告` 标签。',
      ':::',
      '',
      '| 全部 | 重要 | A 股 |',
      '| ---- | ---- | ---- |',
    ].join('\n');
    // Container body + fences and table rows are all dropped → empty prose.
    expect(routeDescriptionProse(desc)).toBe('');

    // Prose outside containers/tables is kept as a single collapsed line.
    const withProse = '::: warning\n需 Cookie\n:::\n\n这是路由说明。';
    expect(routeDescriptionProse(withProse)).toBe('这是路由说明。');
  });

  it('extracts a declared default value from a plain-string description', () => {
    // Path-like default (slash allowed) — chinaratings category.
    expect(parseDefaultFromDescription('分类，默认为 `Industry/Comment`，即行业评论，可在对应分类页 URL 中找到'))
      .toBe('Industry/Comment');
    // Numeric default — jisilu research type.
    expect(parseDefaultFromDescription('分类，默认为 `1`，即宏观经济')).toBe('1');
    // "默认" directly followed by a backtick (no 为/是).
    expect(parseDefaultFromDescription('快讯分类，默认`global`，见下表')).toBe('global');
    // No default declared → empty (do not fall back to a random backtick word).
    expect(parseDefaultFromDescription('`macrodatas` 或 `report`')).toBe('');
    expect(parseDefaultFromDescription('')).toBe('');
  });

  it('flags routes that have a remote option list for the param picker', () => {
    expect(routeNeedsRemoteOptions('/gelonghui/subject/:id')).toBe(true);
    // Routes without a remote list fall back to a plain input.
    expect(routeNeedsRemoteOptions('/wallstreetcn/news/:category?')).toBe(false);
    expect(routeNeedsRemoteOptions('/xueqiu/stock_info/:id')).toBe(false);
  });

  it('hides English-only feeds and keeps Chinese ones in the same namespace', () => {
    // English-only routes are hidden from explore (app is a Chinese reader).
    expect(isEnglishOnly({ route_path: '/finviz/:category?' })).toBe(true);
    expect(isEnglishOnly({ route_path: '/unusualwhales/news' })).toBe(true);
    expect(isEnglishOnly({ route_path: '/bloomberg/:site?' })).toBe(true);
    expect(isEnglishOnly({ route_path: '/blockworks/' })).toBe(true);
    // Same-namespace language split: fastbull/news is Chinese, kept.
    expect(isEnglishOnly({ route_path: '/fastbull/news' })).toBe(false);
    expect(isEnglishOnly({ route_path: '/fastbull/express-news' })).toBe(true);
    // followin is bilingual — news is Chinese (kept), home/kol default English.
    expect(isEnglishOnly({ route_path: '/followin/news/:lang?' })).toBe(false);
    expect(isEnglishOnly({ route_path: '/followin/:categoryId?/:lang?' })).toBe(true);
    expect(isEnglishOnly({ route_path: '/followin/kol/:kolId/:lang?' })).toBe(true);
    // A Chinese route that is neither broken nor English stays visible.
    expect(isHiddenFromExplore({ route_path: '/wallstreetcn/news/:category?' })).toBe(false);
  });

  it('hides both broken and English-only routes via isHiddenFromExplore', () => {
    // Persistently broken route.
    expect(isKnownBroken({ route_path: '/seekingalpha/:symbol/:category?' })).toBe(true);
    expect(isHiddenFromExplore({ route_path: '/seekingalpha/:symbol/:category?' })).toBe(true);
    // English-only route (not broken, but out of scope).
    expect(isKnownBroken({ route_path: '/jpmorganchase/' })).toBe(false);
    expect(isHiddenFromExplore({ route_path: '/jpmorganchase/' })).toBe(true);
    // cs/video: fetches fine but its detail video can't render in-page (fulltext
    // drops controls, http src blocked by mixed content) — hidden as unusable.
    expect(isHiddenFromExplore({ route_path: '/cs/video/:category?' })).toBe(true);
    // xueqiu/stock_info: its stock_timeline.json sits behind Aliyun WAF; even
    // with XUEQIU_COOKIES the JSON endpoint returns the WAF challenge page, so
    // RSSHub 503s on every type. Hidden as persistently broken.
    expect(isHiddenFromExplore({ route_path: '/xueqiu/stock_info/:id/:type?' })).toBe(true);
    // Healthy Chinese route stays visible.
    expect(isHiddenFromExplore({ route_path: '/cls/telegraph/:category?' })).toBe(false);
  });

  it('hides working-but-not-useful feeds via isUnuseful', () => {
    // These return real (Chinese) items but earn no explore slot: off-topic,
    // single-fund, private-id, or duplicate. Distinct from broken/English.
    expect(isUnuseful({ route_path: '/zhizhuan100/analytic' })).toBe(true);
    expect(isUnuseful({ route_path: '/youzhiyouxing/materials/:id?' })).toBe(true);
    expect(isUnuseful({ route_path: '/xueqiu/fund/:id' })).toBe(true);
    expect(isUnuseful({ route_path: '/xueqiu/timeline/:usergroup_id?' })).toBe(true);
    expect(isUnuseful({ route_path: '/wallstreetcn/calendar/:section?' })).toBe(true);
    expect(isUnuseful({ route_path: '/ulapia/research/latest' })).toBe(true);
    expect(isUnuseful({ route_path: '/ulapia/reports/:category?' })).toBe(true);
    // Second batch (2026-07-12): working Chinese feeds, off-scope for explore.
    expect(isUnuseful({ route_path: '/baidu/gushitong/index' })).toBe(true);
    expect(isUnuseful({ route_path: '/bigquant/collections' })).toBe(true);
    expect(isUnuseful({ route_path: '/chinamoney/:channelId?' })).toBe(true);
    expect(isUnuseful({ route_path: '/chinaratings/CreditResearch/:category{.+}?' })).toBe(true);
    expect(isUnuseful({ route_path: '/fastbull/news' })).toBe(true);
    expect(isUnuseful({ route_path: '/futunn/video' })).toBe(true);
    // Third batch (2026-07-12): private-id + off-scope central-bank feeds.
    expect(isUnuseful({ route_path: '/gelonghui/user/:id' })).toBe(true);
    expect(isUnuseful({ route_path: '/gov/pbc/goutongjiaoliu' })).toBe(true);
    expect(isUnuseful({ route_path: '/gov/pbc/gzlw' })).toBe(true);
    // Fourth batch (2026-07-12): community/private-id/regulatory feeds.
    expect(isUnuseful({ route_path: '/huijin-inv/news' })).toBe(true);
    expect(isUnuseful({ route_path: '/jisilu/category/:id' })).toBe(true);
    expect(isUnuseful({ route_path: '/jisilu/explore/:filter?' })).toBe(true);
    expect(isUnuseful({ route_path: '/jisilu/people/:id/:type?' })).toBe(true);
    expect(isUnuseful({ route_path: '/jisilu/topic/:id' })).toBe(true);
    expect(isUnuseful({ route_path: '/jiuyangongshe/community' })).toBe(true);
    expect(isUnuseful({ route_path: '/laohu8/personal/:id' })).toBe(true);
    expect(isUnuseful({ route_path: '/lhratings/research/:type?' })).toBe(true);
    expect(isUnuseful({ route_path: '/mrm/:category?' })).toBe(true);
    expect(isUnuseful({ route_path: '/nbd/daily' })).toBe(true);
    expect(isUnuseful({ route_path: '/sse/convert/:query?' })).toBe(true);
    expect(isUnuseful({ route_path: '/sselawsrules/:category{.+}?' })).toBe(true);
    expect(isUnuseful({ route_path: '/taoguba/:category?' })).toBe(true);
    expect(isUnuseful({ route_path: '/szse/rule/:channel{.+}?' })).toBe(true);
    // Not broken, not English — only flagged as unuseful.
    expect(isKnownBroken({ route_path: '/ulapia/reports/:category?' })).toBe(false);
    expect(isEnglishOnly({ route_path: '/ulapia/reports/:category?' })).toBe(false);
    expect(isHiddenFromExplore({ route_path: '/ulapia/reports/:category?' })).toBe(true);
    // A useful Chinese route stays visible.
    expect(isUnuseful({ route_path: '/cls/telegraph/:category?' })).toBe(false);
  });
});
