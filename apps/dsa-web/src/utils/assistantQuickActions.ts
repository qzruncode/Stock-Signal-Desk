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
    prompt:
      '请从全部 active A 股（沪深北，包含 ST）中筛选：使用 14 日 SMA ATR 相对波动率，以其 60 日 SMA 作为长期均值，动态线为长期均值除以 1.27；当 ATR 相对波动率大于动态线时记为达标。统计最近 250 个交易日，要求至少 175 天达标且达标比例不低于 70%，上市交易历史不少于 250 日；营业收入 TTM 大于 5 亿元、扣非净利润 TTM 大于 0、资产负债率低于 70%。按近 250 日达标比例降序。输出当前 ATR%、60 日长期均值、动态线、250 日达标天数及比例、三项财务指标、财务报告期和来源、行情日期；超过 10 只给完整 CSV。',
  },
] as const;

export const ASSISTANT_QUICK_ACTIONS: readonly { label: string; prompt: string }[] = [
  {
    label: '搜索 / 浏览股票',
    prompt: '搜索新强联，并告诉我股票代码、所属市场和行业',
  },
  {
    label: '检查股票库',
    prompt: '检查股票基础库的覆盖范围、数据时间和新鲜度，告诉我是否需要刷新',
  },
  {
    label: '管理自选股',
    prompt: '查看我的全部自选股，并告诉我如何添加或删除',
  },
  {
    label: '发起 / 跟踪分析',
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
    label: '批量分析',
    prompt: '查看最近的批量分析任务，并告诉我可以执行哪些操作',
  },
  {
    label: '设置定时分析',
    prompt: '查看当前的定时分析计划，并告诉我如何修改',
  },
  {
    label: '发送通知（需确认）',
    prompt: '检查通知配置，并告诉我可以发送哪些分析通知；不要直接发送',
  },
] as const;
