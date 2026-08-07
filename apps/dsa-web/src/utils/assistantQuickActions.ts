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
    id: 'general',
    title: '通用问题',
    description: '解释、写作、总结和开放式问题。',
    items: [
      { label: '把一个复杂概念讲清楚', prompt: '请用大白话解释这个概念，并举一个具体例子：' },
      { label: '帮我润色一段文字', prompt: '请根据用途、读者和语气帮我润色下面这段文字：' },
      { label: '提炼材料的重点', prompt: '请提炼下面材料的结论、依据、不确定性和待办事项：' },
      { label: '解决一个没有模板的问题', prompt: '这是一个不常见的问题。请先理解目标，再根据实际信息决定怎么完成：' },
    ],
  },
  {
    id: 'research',
    title: '研究与核验',
    description: '按问题本身动态取证，不预设股票分析步骤。',
    items: [
      { label: '核验一个最新事实', prompt: '请核验下面这个事实，标明实体、时间口径、来源和证据缺口：' },
      { label: '比较几个研究对象', prompt: '请比较下面几个对象。先说明可比口径，再给事实、推断和不确定性：' },
      { label: '分析一家公司', prompt: '请围绕我真正关心的问题分析这家公司；不要套固定模板，缺什么证据就明确说明：' },
      { label: '研究一个市场问题', prompt: '请研究下面这个市场问题，区分已验证事实、观点、推断和反证：' },
    ],
  },
  {
    id: 'materials',
    title: '资料与数据',
    description: '读取、整理或转换你提供的材料。',
    items: [
      { label: '阅读网页或文件', prompt: '请阅读我接下来提供的网页或文件，并按原始位置引用关键证据：' },
      { label: '从多份资料交叉验证', prompt: '请交叉核验下面这些资料，指出一致、冲突和仍无法确认的部分：' },
      { label: '整理成结构化结果', prompt: '请把下面内容整理成清晰的结构化结果；先确认我需要的字段和用途：' },
    ],
  },
] as const;

export const ASSISTANT_SUGGESTIONS: readonly AssistantSuggestion[] =
  ASSISTANT_SUGGESTION_GROUPS.flatMap((group) => group.items);
