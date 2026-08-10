export interface AssistantSuggestion {
  label: string;
  prompt: string;
}

export interface AssistantSuggestionGroup {
  id: string;
  title: string;
  description: string;
  items: readonly AssistantSuggestion[];
}

/**
 * These are examples of user intent, not a capability catalog or a route to a
 * predefined workflow. The Agent still discovers tools and plans from live
 * state after the text is submitted.
 */
export const ASSISTANT_SUGGESTION_GROUPS: readonly AssistantSuggestionGroup[] = [
  {
    id: 'research',
    title: '常用研究',
    description: '从公司、行业、市场到估值，直接开始投研。',
    items: [
      { label: '研究一家公司基本面', prompt: '请研究这家公司基本面，结合业务、财务、竞争格局、估值和主要风险，标明数据时间与证据缺口：' },
      { label: '比较两家公司', prompt: '请比较下面两家公司，先统一业务、时间和估值口径，再给出事实、差异、推断与不确定性：' },
      { label: '分析一个行业或产业链', prompt: '请研究下面这个行业或产业链，拆解关键环节、受益公司、兑现路径、催化与主要反证：' },
      { label: '拆解估值与主要风险', prompt: '请分析这家公司或股票当前估值，说明估值口径、关键假设、可能的上行空间与主要风险：' },
    ],
  },
  {
    id: 'stock-tools',
    title: '股票工具',
    description: '用行情、资金、筛选和分组落到具体标的。',
    items: [
      { label: '按条件筛选股票', prompt: '请按下面的指标条件筛选股票，说明筛选口径、数据时间、命中结果和可能的遗漏：' },
      { label: '分析一个股票分组', prompt: '请分析这个股票分组，比较成员的基本面、估值、行情、资金和主要风险：' },
      { label: '查看个股行情与资金', prompt: '请查看这只股票近期行情、资金流和重要事件，并说明数据时间、来源与需要谨慎解读的地方：' },
      { label: '比较分组内的股票', prompt: '请比较这个股票分组内的股票，先统一比较口径，再给出差异、排序依据和结论：' },
    ],
  },
  {
    id: 'verification',
    title: '数据核验',
    description: '围绕公告、财报、研报和事件交叉取证。',
    items: [
      { label: '核验最新公告或财报', prompt: '请核验这家公司最新公告或财报，区分已确认事实、管理层表述、推断和仍待验证的问题：' },
      { label: '追踪一个市场事件', prompt: '请追踪下面这个市场事件，整理时间线、涉及主体、最新进展、影响路径和证据来源：' },
      { label: '交叉验证多份资料', prompt: '请交叉核验下面这些资料，指出一致、冲突、时间口径差异和仍无法确认的部分：' },
      { label: '阅读一份年报或研报', prompt: '请阅读这份年报或研报，提炼核心结论、关键数据、假设、风险和原文证据位置：' },
    ],
  },
  {
    id: 'general',
    title: '通用能力',
    description: '解释、写作和整理你提供的材料。',
    items: [
      { label: '解释一个复杂概念', prompt: '请用大白话解释这个概念，并举一个具体例子：' },
      { label: '润色一段文字', prompt: '请根据用途、读者和语气帮我润色下面这段文字：' },
      { label: '提炼材料重点', prompt: '请提炼下面材料的结论、依据、不确定性和待办事项：' },
    ],
  },
] as const;

export const ASSISTANT_SUGGESTIONS: readonly AssistantSuggestion[] =
  ASSISTANT_SUGGESTION_GROUPS.flatMap((group) => group.items);
