// 兼容升级前仍在运行或已持久化的对话：旧版进程标记与新标记都需识别。
const PROCESS_STEPS = [
  {
    marker: '正在拆解问题并规划研究路径...',
    label: '拆解问题与确定研究边界',
  },
  {
    marker: '正在检索和核验关键证据...',
    label: '检索数据并交叉核验证据',
  },
  {
    marker: '正在整理证据并形成结论...',
    label: '整理证据与形成最终结论',
  },
  // 兼容升级前仍在运行或已持久化的对话。
  { marker: '正在理解问题并规划需要查询的数据...', label: '理解问题与规划数据' },
  { marker: '正在调用数据工具...', label: '调用行情与分析工具' },
  { marker: '已完成多轮数据查询，正在生成最终总结...', label: '整理证据与形成结论' },
];

/** 剥离助手回答里的进程标记与停止标记，返回干净正文与命中的进度步骤。 */
export function splitAssistantText(text: string) {
  const steps = PROCESS_STEPS.filter((step) => text.includes(step.marker));
  const stopped = text.includes('[已停止]');
  let content = text;
  for (const step of PROCESS_STEPS) {
    content = content.replaceAll(step.marker, '');
  }
  return {
    steps,
    stopped,
    content: content.replaceAll('[已停止]', '').replace(/^\s+/, '').replace(/\n{3,}/g, '\n\n'),
  };
}
