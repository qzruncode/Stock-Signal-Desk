const ATR_SCREEN_PROMPT =
  '请从全部 active A 股（沪深北，包含 ST）中筛选：使用 14 日 SMA ATR 相对波动率，以其 60 日 SMA 作为长期均值，动态线为长期均值除以 1.27；当 ATR 相对波动率大于动态线时记为达标。统计最近 250 个交易日，要求至少 175 天达标且达标比例不低于 70%，上市交易历史不少于 250 日；营业收入 TTM 大于 5 亿元、扣非净利润 TTM 大于 0、资产负债率低于 70%。按近 250 日达标比例降序。输出当前 ATR%、60 日长期均值、动态线、250 日达标天数及比例、三项财务指标、财务报告期和来源、行情日期；超过 10 只给完整 CSV。';

/**
 * Suggestion chips surfaced on the assistant empty state.
 *
 * `SUGGESTIONS` powers the primary research prompt row; `ASSISTANT_QUICK_ACTIONS`
 * maps to Agent workflow capabilities (search, watchlist, persisted analysis,
 * batch/schedule, templates, notifications) so users can discover the tools that
 * replaced the old Dashboard without typing a prompt.
 */
export const SUGGESTIONS: readonly { label: string; prompt: string }[] = [
  {
    label: '帮我分析下人形机器人产业链，哪些领域最受益？',
    prompt: '帮我分析下人形机器人产业链，哪些领域最受益？',
  },
  {
    label: '比较贵州茅台与五粮液的盈利质量和当前估值',
    prompt: '比较贵州茅台与五粮液的盈利质量和当前估值',
  },
  {
    label: '宁德时代最近有哪些重要公告和风险事件？',
    prompt: '宁德时代最近有哪些重要公告和风险事件？',
  },
  {
    label: '当前 A 股市场宽度如何，哪些板块资金更强？',
    prompt: '当前 A 股市场宽度如何，哪些板块资金更强？',
  },
  {
    label: '按 14 日 ATR 相对波动率与财务条件筛选全部 A 股',
    prompt: ATR_SCREEN_PROMPT,
  },
] as const;

export const ASSISTANT_QUICK_ACTIONS: readonly { label: string; prompt: string }[] = [
  {
    label: '搜索股票',
    prompt: '搜索新强联，并告诉我股票代码、所属市场和行业',
  },
  {
    label: '检查股票库',
    prompt: '检查股票基础库的覆盖范围、数据时间和新鲜度，告诉我是否需要刷新',
  },
  {
    label: '查看自选分组',
    prompt: '列出我的全部自选分组、每组股票数量和成员',
  },
  {
    label: '创建自选分组',
    prompt: '创建一个名为“核心观察”的自选分组，先不要添加股票',
  },
  {
    label: '维护分组成员',
    prompt: '先列出我的自选分组；我随后会指定分组并要求添加或移除股票',
  },
  {
    label: 'ATR 筛选并入组',
    prompt: `${ATR_SCREEN_PROMPT} 将完整筛选结果保存为“高波动观察”自选分组。`,
  },
  {
    label: '发起股票分析',
    prompt: '分析宁德时代，保存正式报告，完成后告诉我',
  },
  {
    label: '查看分析进度',
    prompt: '查看我正在运行和最近完成的分析任务',
  },
  {
    label: '查历史报告',
    prompt: '查找并读取最近完成的股票分析报告',
  },
  {
    label: '管理分析模板',
    prompt: '列出我的分析模板，并告诉我当前默认模板及其用途',
  },
  {
    label: '按分组批量分析',
    prompt: '列出我的自选分组；我随后会指定一个分组发起批量分析',
  },
  {
    label: '控制批量任务',
    prompt: '查看最近的批量分析任务及进度，并列出可暂停、继续或重试的任务',
  },
  {
    label: '设置定时分析',
    prompt: '查看当前的定时分析计划，并告诉我如何修改',
  },
  {
    label: '检查通知配置',
    prompt: '检查通知配置，并告诉我可以发送哪些分析通知；不要直接发送',
  },
  {
    label: '发送分析通知',
    prompt: '先列出最近完成的分析报告；我确认具体报告后再发送通知',
  },
  {
    label: '搜索股市资讯',
    prompt: '搜索今天最重要的 A 股市场资讯，按主题归类并标明来源和时间',
  },
  {
    label: '浏览全部资讯源',
    prompt: '列出助手可用的全部股市资讯与 RSS 源，按命名空间和用途分组，不要按关键词过滤',
  },
  {
    label: '读取完整资讯',
    prompt: '先搜索宁德时代最近的重要资讯，我指定一条后再读取完整正文',
  },
] as const;
