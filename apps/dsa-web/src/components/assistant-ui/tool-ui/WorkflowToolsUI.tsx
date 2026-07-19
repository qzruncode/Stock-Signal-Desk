import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import { BellIcon, FileTextIcon, ListChecksIcon, PlayIcon, Settings2Icon } from 'lucide-react';
import { ToolStatusPill } from './shared';

type WorkflowResult = {
  success?: boolean;
  accepted?: boolean;
  action?: string;
  task_id?: string;
  stock_code?: string;
  status?: string;
  progress?: number;
  message?: string;
  item_count?: number;
  returned_count?: number;
  total?: number;
  items?: Array<Record<string, unknown>>;
  report?: Record<string, unknown>;
  markdown_length?: number;
  linked_news_count?: number;
  schedule?: Record<string, unknown>;
  channels?: Array<Record<string, unknown>>;
  configured_count?: number;
  channel?: string;
  sent?: boolean;
  deleted_count?: number;
  errors?: string[];
};

const LABELS: Record<string, { running: string; done: string }> = {
  run_stock_analysis: { running: '正在提交正式分析任务', done: '正式分析任务已提交' },
  get_analysis_status: { running: '正在查询分析进度', done: '分析任务状态已更新' },
  search_analysis_history: { running: '正在查询历史报告', done: '历史报告查询完成' },
  read_analysis_report: { running: '正在读取正式报告', done: '正式报告已读取' },
  delete_analysis_history: { running: '正在删除分析历史', done: '分析历史已删除' },
  manage_analysis_templates: { running: '正在管理分析模板', done: '分析模板已更新' },
  run_batch_analysis: { running: '正在启动批量分析', done: '批量分析已启动' },
  manage_batch_run: { running: '正在处理批量任务', done: '批量任务已更新' },
  manage_analysis_schedule: { running: '正在处理自动分析计划', done: '自动分析计划已更新' },
  get_notification_status: { running: '正在检查通知配置', done: '通知配置检查完成' },
  send_notification: { running: '正在发送通知', done: '通知已发送' },
};

function text(value: unknown): string {
  return value == null ? '' : String(value);
}

export default function WorkflowToolsUI({
  toolName,
  result,
  status,
  isError,
}: ToolCallMessagePartProps<Record<string, unknown>, WorkflowResult | undefined>) {
  const labels = LABELS[toolName] ?? { running: '正在执行工作流', done: '工作流已完成' };
  if (status.type === 'running' && !result) return <ToolStatusPill status={status} label={labels.running} />;
  if (isError || result?.success === false || (status.type === 'incomplete' && status.reason === 'error')) {
    return <ToolStatusPill status={status} isError label={result?.errors?.[0] || `${labels.done}失败`} />;
  }

  const isNotification = toolName.includes('notification');
  const isReport = toolName.includes('report') || toolName.includes('history');
  const isSettings = toolName.includes('template') || toolName.includes('schedule');
  const Icon = isNotification ? BellIcon : isReport ? FileTextIcon : isSettings ? Settings2Icon : toolName.includes('batch') ? ListChecksIcon : PlayIcon;
  const taskId = result?.task_id;
  const count = result?.returned_count ?? result?.item_count ?? result?.total;

  return (
    <div className="my-2 rounded-xl border border-border bg-card/70 p-3 text-xs shadow-sm">
      <div className="flex items-center gap-2 font-semibold text-foreground">
        <span className="flex size-7 items-center justify-center rounded-lg bg-primary/10 text-primary"><Icon className="size-4" /></span>
        {result?.message || labels.done}
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5 text-[10px] text-muted-foreground">
        {result?.stock_code && <span className="rounded bg-muted px-2 py-1">{result.stock_code}</span>}
        {taskId && <span className="rounded bg-muted px-2 py-1">任务 {taskId}</span>}
        {result?.status && <span className="rounded bg-muted px-2 py-1">状态 {result.status}</span>}
        {result?.progress != null && <span className="rounded bg-muted px-2 py-1">进度 {result.progress}%</span>}
        {count != null && <span className="rounded bg-muted px-2 py-1">{count} 项</span>}
        {result?.markdown_length != null && <span className="rounded bg-muted px-2 py-1">报告 {result.markdown_length} 字</span>}
        {result?.linked_news_count != null && <span className="rounded bg-muted px-2 py-1">关联资讯 {result.linked_news_count} 条</span>}
        {result?.sent && <span className="rounded bg-emerald-100 px-2 py-1 text-emerald-700">已发送至 {result.channel || '通知渠道'}</span>}
        {result?.deleted_count != null && <span className="rounded bg-red-50 px-2 py-1 text-red-700">已删除 {result.deleted_count} 条</span>}
      </div>
      {toolName === 'get_notification_status' && (
        <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
          已配置 {result?.configured_count ?? 0} 个渠道。通知凭据请在设置页维护。
        </p>
      )}
      {result?.schedule && (
        <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
          计划：{text(result.schedule.enabled) === 'true' ? '已启用' : '已停用'} · {text(result.schedule.times) || '暂无时间'}
        </p>
      )}
    </div>
  );
}
