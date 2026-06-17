import { useCallback, useEffect, useMemo } from 'react';
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, LoaderCircle, RefreshCw } from 'lucide-react';
import { Badge, Button, Card, InlineAlert } from '../common';
import { useBuyDecisionWorkbench } from '../../hooks';
import type { BuyDecisionStepActivity } from '../../hooks';
import type { BuyDecisionStepKey, BuyDecisionWorkbenchResponse } from '../../api/buyDecisionWorkbench';
import { IndustryBetaChat } from './IndustryBetaChat';

const STEP_ORDER: BuyDecisionStepKey[] = [
  'profile_mapping',
  'industry_beta',
  'mainline_position',
  'company_benefit',
  'buy_constraints',
  'final_decision',
];

const STEP_META: Record<BuyDecisionStepKey, { title: string; description: string }> = {
  profile_mapping: {
    title: '个股基础面与行业归属',
    description: '确认主营、行业归属和行业映射是否成立。',
  },
  industry_beta: {
    title: '行业周期与行业 β',
    description: '确认行业景气、空间、价格战风险和驱动因素。',
  },
  mainline_position: {
    title: '主线属性与市场位置',
    description: '判断市场位置、主题匹配和催化持续性。',
  },
  company_benefit: {
    title: '个股真实受益与弹性',
    description: '确认主营受益、财务状态、估值和弹性信号。',
  },
  buy_constraints: {
    title: '风险与买点约束',
    description: '识别追高、位置、估值和事件风险。',
  },
  final_decision: {
    title: '最终模型结论',
    description: '汇总完整证据包，输出最终买入判断。',
  },
};

type MetricItem = {
  label: string;
  value: string;
  tone?: 'default' | 'warning' | 'danger' | 'success';
};

function formatJoinedList(value: unknown, emptyValue: string = '-'): string {
  if (!Array.isArray(value)) return emptyValue;
  const items = value.map((item) => String(item).trim()).filter(Boolean);
  return items.length ? items.join(' / ') : emptyValue;
}

function renderDecisionBadgeTone(decision: string): 'success' | 'warning' | 'danger' | 'info' {
  if (decision === '可买入') return 'success';
  if (decision === '禁止追高') return 'danger';
  if (decision === '可跟踪等待') return 'warning';
  return 'info';
}

function renderShortList(items: string[], emptyText: string) {
  if (!items.length) {
    return <p className="text-sm text-slate-500">{emptyText}</p>;
  }
  return (
    <div className="space-y-1.5">
      {items.map((item) => (
        <p key={item} className="text-sm leading-6 text-slate-700">{item}</p>
      ))}
    </div>
  );
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value ?? '');
  }
}

function getStepDotTone(status: string, isActive: boolean): string {
  if (status === 'success') return 'border-emerald-200 bg-emerald-500 text-white';
  if (status === 'running' || isActive) return 'border-cyan-200 bg-cyan-500 text-white';
  return 'border-slate-200 bg-white text-slate-400';
}

function formatMetricValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2);
  return String(value);
}

function getStatusBadge(status: string) {
  if (status === 'success') return <Badge variant="success">成功</Badge>;
  if (status === 'running') return <Badge variant="info">执行中</Badge>;
  if (status === 'failed') return <Badge variant="danger">失败</Badge>;
  return <Badge variant="default">未开始</Badge>;
}

function buildStepMetrics(
  stepKey: BuyDecisionStepKey,
  state: BuyDecisionWorkbenchResponse['steps'][BuyDecisionStepKey] | undefined,
): MetricItem[] {
  const data = state?.data as Record<string, unknown> | undefined;
  if (!data) return [];

  if (stepKey === 'profile_mapping') {
    const profile = data.stock_profile as Record<string, unknown> | undefined;
    const mapping = data.industry_mapping as Record<string, unknown> | undefined;
    return [
      { label: '所属行业', value: formatMetricValue(profile?.industry_name) },
      { label: '主营业务', value: formatMetricValue(profile?.main_business ? '已获取' : '-') },
      { label: '映射强度', value: formatMetricValue(mapping?.industry_match_level) },
    ];
  }

  if (stepKey === 'industry_beta') {
    const evidence = data.industry_beta_evidence as Record<string, unknown> | undefined;
    const sector = evidence?.sector_snapshot as Record<string, unknown> | undefined;
    const flow = evidence?.fund_flow as Record<string, unknown> | undefined;
    const priceWar = evidence?.price_war_signal as Record<string, unknown> | undefined;
    return [
      { label: '板块排名', value: `${formatMetricValue(sector?.rank)}/${formatMetricValue(sector?.total)}` },
      { label: '板块涨跌', value: `${formatMetricValue(sector?.change_pct)}%` },
      { label: '资金流排名', value: `${formatMetricValue(flow?.rank)}/${formatMetricValue(flow?.total)}` },
      { label: '价格战风险', value: formatMetricValue(priceWar?.level) },
    ];
  }

  if (stepKey === 'mainline_position') {
    const evidence = data.mainline_evidence as Record<string, unknown> | undefined;
    const market = evidence?.market_mainline as Record<string, unknown> | undefined;
    const heat = evidence?.heat_snapshot as Record<string, unknown> | undefined;
    const concept = evidence?.concept_purity as Record<string, unknown> | undefined;
    const catalyst = evidence?.catalyst_snapshot as Record<string, unknown> | undefined;
    return [
      { label: '主线归属', value: formatMetricValue(market?.current_status) },
      { label: '热度等级', value: formatMetricValue(heat?.heat_level) },
      { label: '催化数量', value: formatMetricValue(catalyst?.count) },
      { label: '概念纯度', value: formatMetricValue(concept?.level) },
    ];
  }

  if (stepKey === 'company_benefit') {
    const evidence = data.company_evidence as Record<string, unknown> | undefined;
    const focus = evidence?.stock_focus_snapshot as Record<string, unknown> | undefined;
    const valuation = evidence?.valuation_snapshot as Record<string, unknown> | undefined;
    const elasticity = evidence?.elasticity_signals as unknown[] | undefined;
    return [
      { label: '受益对齐', value: formatMetricValue(evidence?.benefit_alignment ? '已确认' : '-') },
      { label: '财务状态', value: formatMetricValue(focus?.finance_state) },
      { label: '估值状态', value: formatMetricValue(valuation?.valuation_status) },
      { label: '弹性信号', value: formatMetricValue(elasticity?.length ?? 0) },
    ];
  }

  if (stepKey === 'buy_constraints') {
    const constraints = data.buy_constraints as Record<string, unknown> | undefined;
    return [
      { label: '追高风险', value: formatMetricValue(constraints?.chasing_risk) },
      { label: '位置风险', value: formatMetricValue(constraints?.position_risk) },
      { label: '估值风险', value: formatMetricValue(constraints?.valuation_risk) },
      { label: '事件风险', value: formatMetricValue(constraints?.event_risk) },
    ];
  }

  if (stepKey === 'final_decision') {
    const decision = data.buy_decision as Record<string, unknown> | undefined;
    const cycle = data.industry_cycle as Record<string, unknown> | undefined;
    return [
      { label: '买入判断', value: formatMetricValue(decision?.decision) },
      { label: '进入方式', value: formatMetricValue(decision?.entry_type) },
      { label: '周期标签', value: formatMetricValue(cycle?.analysis_status) },
      { label: '景气分', value: formatMetricValue(cycle?.prosperity_score) },
    ];
  }

  return [];
}

function DetailBlock({
  title,
  lines,
}: {
  title: string;
  lines: Array<{ label: string; value: string }>;
}) {
  const visibleLines = lines.filter((line) => line.value && line.value !== '-');
  if (!visibleLines.length) return null;
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3 py-3">
      <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-400">{title}</p>
      <div className="mt-2 space-y-2">
        {visibleLines.map((line) => (
          <div key={`${title}-${line.label}`} className="grid grid-cols-[5rem_1fr] gap-3 text-sm">
            <span className="text-slate-400">{line.label}</span>
            <span className="break-words text-slate-700">{line.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function renderStepDetails(
  stepKey: BuyDecisionStepKey,
  state: BuyDecisionWorkbenchResponse['steps'][BuyDecisionStepKey] | undefined,
) {
  const data = state?.data as Record<string, unknown> | undefined;
  if (!data) return null;

  if (stepKey === 'profile_mapping') {
    const profile = (data.stock_profile as Record<string, unknown> | undefined) ?? {};
    const mapping = (data.industry_mapping as Record<string, unknown> | undefined) ?? {};
    return (
      <div className="grid gap-2">
        <DetailBlock
          title="基础资料"
          lines={[
            { label: '股票', value: formatMetricValue(profile.stock_name) },
            { label: '行业', value: formatMetricValue(profile.industry_name) },
            { label: '主营', value: formatMetricValue(profile.main_business) },
            { label: '市场', value: formatMetricValue(profile.exchange) },
          ]}
        />
        <DetailBlock
          title="行业映射"
          lines={[
            { label: '相关性', value: formatMetricValue(mapping.is_industry_relevant ? '是' : '否') },
            { label: '强度', value: formatMetricValue(mapping.industry_match_level) },
            { label: '说明', value: formatMetricValue(mapping.mapping_reason) },
            { label: '主营词', value: Array.isArray(mapping.business_keywords) ? mapping.business_keywords.join(' / ') : '-' },
          ]}
        />
      </div>
    );
  }

  if (stepKey === 'industry_beta') {
    const evidence = (data.industry_beta_evidence as Record<string, unknown> | undefined) ?? {};
    const sector = (evidence.sector_snapshot as Record<string, unknown> | undefined) ?? {};
    const flow = (evidence.fund_flow as Record<string, unknown> | undefined) ?? {};
    const space = (evidence.industry_space as Record<string, unknown> | undefined) ?? {};
    const war = (evidence.price_war_signal as Record<string, unknown> | undefined) ?? {};
    const drivers = (evidence.driver_signals as Record<string, unknown> | undefined) ?? {};
    return (
      <div className="grid gap-2">
        <DetailBlock
          title="行业景气"
          lines={[
            { label: '板块', value: formatMetricValue(sector.name) },
            { label: '涨跌', value: `${formatMetricValue(sector.change_pct)}%` },
            { label: '排名', value: `${formatMetricValue(sector.rank)}/${formatMetricValue(sector.total)}` },
            { label: '资金流', value: formatMetricValue(flow.net_inflow) },
          ]}
        />
        <DetailBlock
          title="行业约束"
          lines={[
            { label: '空间', value: formatMetricValue(space.space_level) },
            { label: '空间说明', value: formatMetricValue(space.reason) },
            { label: '价格战', value: formatMetricValue(war.level) },
            { label: '驱动', value: Object.entries(drivers).flatMap(([, values]) => Array.isArray(values) ? values.map(String) : []).join(' / ') || '-' },
          ]}
        />
      </div>
    );
  }

  if (stepKey === 'mainline_position') {
    const evidence = (data.mainline_evidence as Record<string, unknown> | undefined) ?? {};
    const market = (evidence.market_mainline as Record<string, unknown> | undefined) ?? {};
    const heat = (evidence.heat_snapshot as Record<string, unknown> | undefined) ?? {};
    const concept = (evidence.concept_purity as Record<string, unknown> | undefined) ?? {};
    const catalyst = (evidence.catalyst_snapshot as Record<string, unknown> | undefined) ?? {};
    const themes = Array.isArray(evidence.theme_matches) ? evidence.theme_matches.map(String).join(' / ') : '-';
    return (
      <div className="grid gap-2">
        <DetailBlock
          title="市场位置"
          lines={[
            { label: '状态', value: formatMetricValue(market.current_status) },
            { label: '主题', value: formatMetricValue(market.matched_theme) },
            { label: '匹配', value: themes },
            { label: '热度', value: `${formatMetricValue(heat.heat_level)} / ${formatMetricValue(heat.discussion_count)}` },
          ]}
        />
        <DetailBlock
          title="催化与纯度"
          lines={[
            { label: '纯度', value: formatMetricValue(concept.level) },
            { label: '说明', value: formatMetricValue(concept.reason) },
            { label: '催化数', value: formatMetricValue(catalyst.count) },
            { label: '催化', value: Array.isArray(catalyst.items) ? catalyst.items.map(String).join(' / ') : '-' },
          ]}
        />
      </div>
    );
  }

  if (stepKey === 'company_benefit') {
    const evidence = (data.company_evidence as Record<string, unknown> | undefined) ?? {};
    const financial = (evidence.financial_snapshot as Record<string, unknown> | undefined) ?? {};
    const valuation = (evidence.valuation_snapshot as Record<string, unknown> | undefined) ?? {};
    const focus = (evidence.stock_focus_snapshot as Record<string, unknown> | undefined) ?? {};
    const elasticity = Array.isArray(evidence.elasticity_signals) ? evidence.elasticity_signals.map(String).join(' / ') : '-';
    return (
      <div className="grid gap-2">
        <DetailBlock
          title="受益判断"
          lines={[
            { label: '焦点', value: formatMetricValue(focus.focus_view) },
            { label: '受益', value: formatMetricValue(evidence.benefit_alignment) },
            { label: '绑定', value: formatMetricValue(focus.business_binding_strength) },
            { label: '财务', value: formatMetricValue(focus.finance_state) },
            { label: '股东', value: formatMetricValue(focus.holder_state) },
            { label: '交易', value: formatMetricValue(focus.trading_state) },
            { label: '证据强度', value: formatMetricValue(focus.direct_evidence_strength) },
          ]}
        />
        <DetailBlock
          title="弹性与估值"
          lines={[
            { label: '营收增速', value: formatMetricValue(financial.revenue_yoy) },
            { label: '利润增速', value: formatMetricValue(financial.profit_yoy) },
            { label: 'PE', value: formatMetricValue(valuation.pe_ttm) },
            { label: '透支', value: formatMetricValue(valuation.price_overdraft_status ?? valuation.valuation_status) },
            { label: '弹性', value: elasticity },
          ]}
        />
        <DetailBlock
          title="个股证据点"
          lines={[
            { label: '财务点', value: formatJoinedList(focus.finance_points) },
            { label: '股东点', value: formatJoinedList(focus.holder_points) },
            { label: '交易点', value: formatJoinedList(focus.trading_points) },
          ]}
        />
        <DetailBlock
          title="情绪与风险"
          lines={[
            { label: '研报数', value: formatMetricValue((evidence.sentiment_snapshot as Record<string, unknown> | undefined)?.research_count) },
            { label: '正向研报', value: formatMetricValue((evidence.sentiment_snapshot as Record<string, unknown> | undefined)?.positive_research_count) },
            { label: '讨论度', value: formatMetricValue((evidence.sentiment_snapshot as Record<string, unknown> | undefined)?.discussion_count) },
            { label: '风险标签', value: formatJoinedList((evidence.risk_snapshot as Record<string, unknown> | undefined)?.top_risk_labels) },
          ]}
        />
      </div>
    );
  }

  if (stepKey === 'buy_constraints') {
    const constraints = (data.buy_constraints as Record<string, unknown> | undefined) ?? {};
    const risk = (constraints.risk_snapshot as Record<string, unknown> | undefined) ?? {};
    const sentiment = (constraints.sentiment_snapshot as Record<string, unknown> | undefined) ?? {};
    const points = Array.isArray(constraints.observation_points) ? constraints.observation_points.map(String).join(' / ') : '-';
    return (
      <div className="grid gap-2">
        <DetailBlock
          title="风险约束"
          lines={[
            { label: '高风险', value: formatMetricValue(risk.high_risk_count) },
            { label: '中风险', value: formatMetricValue(risk.medium_risk_count) },
            { label: '追高', value: formatMetricValue(constraints.chasing_risk) },
            { label: '位置', value: formatMetricValue(constraints.position_risk) },
            { label: '事件', value: formatMetricValue(constraints.event_risk) },
          ]}
        />
        <DetailBlock
          title="情绪与观察"
          lines={[
            { label: '情绪分', value: formatMetricValue(sentiment.sentiment_score) },
            { label: '社交分', value: formatMetricValue(sentiment.social_score) },
            { label: '新闻数', value: formatMetricValue(sentiment.news_count) },
            { label: '讨论度', value: formatMetricValue(sentiment.discussion_count) },
            { label: '估值风险', value: formatMetricValue(constraints.valuation_risk) },
            { label: '透支分', value: formatMetricValue(constraints.valuation_score) },
            { label: '观察点', value: points },
          ]}
        />
        <DetailBlock
          title="核心风险标签"
          lines={[
            { label: '标签', value: formatJoinedList(risk.top_risk_labels) },
          ]}
        />
      </div>
    );
  }

  if (stepKey === 'final_decision') {
    const decision = (data.buy_decision as Record<string, unknown> | undefined) ?? {};
    const cycle = (data.industry_cycle as Record<string, unknown> | undefined) ?? {};
    return (
      <div className="grid gap-2">
        <DetailBlock
          title="买入结论"
          lines={[
            { label: '判断', value: formatMetricValue(decision.decision) },
            { label: '方式', value: formatMetricValue(decision.entry_type) },
            { label: '理由', value: formatMetricValue(decision.decision_reason) },
            { label: '总结', value: formatMetricValue(data.final_summary) },
          ]}
        />
        <DetailBlock
          title="底层标签"
          lines={[
            { label: '周期标签', value: formatMetricValue(cycle.analysis_status) },
            { label: '周期阶段', value: formatMetricValue(cycle.cycle_phase) },
            { label: '景气分', value: formatMetricValue(cycle.prosperity_score) },
            { label: '逻辑', value: formatMetricValue(cycle.core_logic) },
          ]}
        />
      </div>
    );
  }

  return null;
}

function canRunStep(
  workbench: BuyDecisionWorkbenchResponse | null,
  stepKey: BuyDecisionStepKey,
): boolean {
  if (!workbench) return false;
  const index = STEP_ORDER.indexOf(stepKey);
  return STEP_ORDER.slice(0, index).every((previous) => workbench.steps[previous]?.status === 'success');
}

function StepChecklist({ title, checklist }: { title: string; checklist: Array<Record<string, unknown>> }) {
  if (!checklist.length) return null;
  return (
    <div className="mt-3 space-y-2">
      <p className="text-xs font-medium uppercase tracking-[0.16em] text-slate-400">{title}</p>
      <div className="space-y-2">
        {checklist.map((item, index) => {
          const passed = Boolean(item.passed);
          return (
            <div key={`${title}-${index}`} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
              <div className="flex items-start justify-between gap-3">
                <span className="text-sm font-medium text-slate-800">{formatMetricValue(item.item)}</span>
                <span className={`text-xs font-medium ${passed ? 'text-emerald-700' : 'text-amber-700'}`}>
                  {passed ? '通过' : '未通过'}
                </span>
              </div>
              <p className="mt-1 text-xs leading-5 text-slate-500">{formatMetricValue(item.reason)}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function formatLogTime(value: string) {
  try {
    return new Date(value).toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '--:--:--';
  }
}

function getLogTone(kind: BuyDecisionStepActivity['logs'][number]['kind']) {
  if (kind === 'success') return 'border-emerald-200 bg-emerald-50 text-emerald-900';
  if (kind === 'error') return 'border-rose-200 bg-rose-50 text-rose-900';
  if (kind === 'data') return 'border-slate-200 bg-white text-slate-900';
  if (kind === 'query') return 'border-cyan-200 bg-cyan-50 text-cyan-950';
  return 'border-slate-200 bg-slate-50 text-slate-700';
}

function getLogLabel(kind: BuyDecisionStepActivity['logs'][number]['kind']) {
  if (kind === 'success') return '结果';
  if (kind === 'error') return '错误';
  if (kind === 'data') return '数据';
  if (kind === 'query') return '执行';
  return '系统';
}

function StepExecutionPanel({ activity }: { activity: BuyDecisionStepActivity }) {
  const statusText = activity.status === 'running'
    ? (activity.force ? '正在重新执行本步' : '正在执行本步')
    : activity.status === 'failed'
      ? '本次执行中断'
      : '本次执行已完成';

  return (
    <div className="space-y-3 rounded-2xl border border-slate-200 bg-white p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">Analysis Trace</p>
          <p className="mt-1 text-sm font-medium text-slate-900">{statusText}</p>
          <p className="mt-1 text-xs leading-5 text-slate-500">逐条展示数据获取、证据整理、研判输出和原始片段。</p>
        </div>
        {activity.status === 'running' ? <Badge variant="info">实时追加</Badge> : <Badge variant="default">本次记录</Badge>}
      </div>

      <div className="space-y-2">
        {activity.logs.map((log) => {
          const hasExtra = Boolean(log.detail || log.analysis || log.raw !== undefined);
          return (
            <details key={log.id} open={activity.status === 'running' && log === activity.logs[activity.logs.length - 1]} className="group rounded-xl border border-slate-200 bg-slate-50/70">
              <summary className="flex cursor-pointer list-none gap-3 px-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="flex items-start gap-2">
                    <span className="mt-2 h-2 w-2 shrink-0 rounded-full bg-slate-300" />
                    <span className={`mt-0.5 inline-flex shrink-0 rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${getLogTone(log.kind)}`}>
                      {getLogLabel(log.kind)}
                    </span>
                    <p className="min-w-0 flex-1 truncate font-mono text-[13px] leading-6 text-slate-900">{log.message}</p>
                    <span className="shrink-0 pt-1 text-[11px] font-medium text-slate-400">{formatLogTime(log.at)}</span>
                    {activity.status === 'running' && log === activity.logs[activity.logs.length - 1] ? (
                      <LoaderCircle className="mt-1 h-3.5 w-3.5 shrink-0 animate-spin text-cyan-600" />
                    ) : null}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
                    {log.source ? <span>{log.source}</span> : null}
                    {hasExtra ? <span>展开查看细节</span> : null}
                  </div>
                </div>
              </summary>
              {hasExtra ? (
                <div className="border-t border-slate-200 bg-white px-3 py-3">
                  {log.detail ? <p className="text-xs leading-5 text-slate-500">{log.detail}</p> : null}
                  {log.analysis ? (
                    <div className="mt-2 rounded-lg bg-slate-50 px-2.5 py-2 text-xs leading-5 text-slate-700">
                      <span className="font-medium text-slate-500">研判</span>
                      <span className="ml-2">{log.analysis}</span>
                    </div>
                  ) : null}
                  {log.raw !== undefined ? (
                    <details className="mt-2 rounded-lg border border-slate-200 bg-white">
                      <summary className="cursor-pointer list-none px-2.5 py-2 text-xs font-medium text-slate-600">
                        原始数据片段
                      </summary>
                      <div className="border-t border-slate-200 px-2.5 py-2">
                        <pre className="overflow-x-auto overflow-y-auto whitespace-pre rounded bg-slate-950 p-3 font-mono text-[11px] leading-5 text-slate-100">{prettyJson(log.raw)}</pre>
                      </div>
                    </details>
                  ) : null}
                </div>
              ) : null}
            </details>
          );
        })}
      </div>

      {(activity.staleSummary || activity.staleData) ? (
        <details className="rounded-xl border border-slate-200 bg-white px-3 py-2">
          <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-medium text-slate-700">
            上一轮结果
            <ChevronDown className="h-4 w-4 text-slate-400" />
          </summary>
          {activity.staleSummary ? (
            <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
              {activity.staleSummary}
            </div>
          ) : null}
          {activity.staleData ? (
            <div className="mt-3 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-3">
              <pre className="whitespace-pre-wrap break-words text-xs leading-5 text-slate-700">{prettyJson(activity.staleData)}</pre>
            </div>
          ) : null}
        </details>
      ) : null}
    </div>
  );
}

/**
 * TODO: 待重做 — 2026-06-16
 * 当前实现需要重新设计：证据收集流程、UI 交互模式、步骤编排逻辑都需要优化。
 * 在重做前不要进行功能扩展。
 */
export default function BuyDecisionWorkbench({ symbol }: { symbol: string | null }) {
  const {
    state,
    report,
    error,
    initializing,
    runningStep,
    stepActivities,
    initialize,
    runStep,
    loadReport,
  } = useBuyDecisionWorkbench();

  useEffect(() => {
    if (!symbol) {
      return;
    }
    void initialize(symbol, false);
  }, [initialize, symbol]);

  useEffect(() => {
    if (state?.steps.final_decision?.status === 'success' && !report) {
      void loadReport();
    }
  }, [loadReport, report, state?.steps.final_decision?.status]);

  const activeStep = useMemo(() => {
    if (!state) return 'profile_mapping' as BuyDecisionStepKey;
    return STEP_ORDER.find((step) => state.steps[step]?.status !== 'success') ?? 'final_decision';
  }, [state]);

  const completedStepCount = useMemo(
    () => STEP_ORDER.filter((step) => state?.steps[step]?.status === 'success').length,
    [state],
  );
  const activeStepState = state?.steps[activeStep];
  const activeStepCanRun = canRunStep(state, activeStep);

  const handleRunStep = useCallback(async (stepKey: BuyDecisionStepKey, force: boolean = false) => {
    const result = await runStep(stepKey, force);
    if (stepKey === 'final_decision' && result) {
      await loadReport();
    }
  }, [loadReport, runStep]);

  if (!symbol) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
        <p className="text-sm text-slate-500">先选择股票，再进入买入判断。</p>
      </div>
    );
  }

  if (initializing || !state) {
    return (
      <div className="flex h-40 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <LoaderCircle className="h-8 w-8 animate-spin text-cyan-600" />
          <span className="text-sm text-slate-400">正在初始化买入判断工作台...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 pb-28 md:pb-6">
      {error ? (
        <InlineAlert
          title="买入判断执行失败"
          variant="danger"
          message={error}
          action={(
            <Button variant="outline" size="sm" onClick={() => void initialize(symbol, true)}>
              重新初始化
            </Button>
          )}
        />
      ) : null}

      {/* TODO: 待重做 — 当前版本待废弃 */}
      <InlineAlert
        title="页面待重做"
        variant="warning"
        message="当前买入判断页面的交互流程和证据展示方式正在重新设计，请勿在此基础上扩展功能。"
      />

      <Card className="sticky top-0 z-10 space-y-3 border border-slate-200/80 bg-white/95 backdrop-blur" padding="lg">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-400">Buy Decision</p>
            <h2 className="mt-1 text-xl font-semibold text-slate-950">买入判断</h2>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              分步骤收集证据，最后统一生成买入结论。中间步骤只给证据，不提前替你下最终结论。
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => void initialize(symbol, true)}>
            <RefreshCw className="h-4 w-4" />
            重置
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <Badge variant="default">{state.stock_name || symbol}</Badge>
          <Badge variant="info">{state.symbol}</Badge>
          {state.industry_name ? <Badge variant="warning">{state.industry_name}</Badge> : null}
        </div>
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-3">
          <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-400">当前进度</p>
          <div className="mt-2 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium text-slate-900">{STEP_META[activeStep].title}</p>
              <p className="mt-1 text-xs leading-5 text-slate-500">{STEP_META[activeStep].description}</p>
            </div>
            <div className="shrink-0 rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-600">
              {completedStepCount}/{STEP_ORDER.length}
            </div>
          </div>
        </div>
      </Card>

      {STEP_ORDER.map((stepKey, index) => {
        const stepState = state.steps[stepKey];
        const metrics = buildStepMetrics(stepKey, stepState);
        const stepActivity = stepActivities[stepKey];
        const expanded = stepKey === activeStep || stepState.status === 'running' || runningStep === stepKey || stepActivity?.status === 'running' || (stepKey === 'final_decision' && Boolean(report));
        const showExecutionLog = Boolean(stepActivity);
        const detectorChecklist = stepKey === 'industry_beta'
          ? (((stepState.data as Record<string, unknown> | undefined)?.industry_beta_detector as Record<string, unknown> | undefined)?.checklist as Array<Record<string, unknown>> | undefined) ?? []
          : stepKey === 'mainline_position'
            ? (((stepState.data as Record<string, unknown> | undefined)?.mainline_detector as Record<string, unknown> | undefined)?.checklist as Array<Record<string, unknown>> | undefined) ?? []
            : [];
        const runAllowed = canRunStep(state, stepKey);

        return (
          <div key={stepKey} className="relative pl-9">
            {index < STEP_ORDER.length - 1 ? (
              <div className="absolute bottom-[-16px] left-[15px] top-8 w-px bg-slate-200" />
            ) : null}
            <div className={`absolute left-0 top-5 flex h-8 w-8 items-center justify-center rounded-full border text-xs font-semibold ${getStepDotTone(stepState.status, stepKey === activeStep)}`}>
              {stepState.status === 'success' ? <CheckCircle2 className="h-4 w-4" /> : index + 1}
            </div>
            <Card className={`space-y-3 ${stepKey === activeStep ? 'border-cyan-200 shadow-sm' : ''}`} padding="lg">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-slate-900">{index + 1}. {STEP_META[stepKey].title}</span>
                    {stepKey === activeStep ? <Badge variant="info">当前</Badge> : null}
                    {stepState.status === 'success' ? <Badge variant="success">下一步已解锁</Badge> : null}
                  </div>
                  <p className="mt-1 text-sm leading-6 text-slate-500">{STEP_META[stepKey].description}</p>
                </div>
                {getStatusBadge(stepState.status)}
              </div>

              {stepKey === 'industry_beta' && (stepKey === activeStep || stepActivity?.status === 'running') ? (
                <IndustryBetaChat symbol={symbol} sessionId={state.session_id} />
              ) : null}

              {stepState.summary && !showExecutionLog ? (
                <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
                  {stepState.summary}
                </div>
              ) : null}

              {stepState.error ? (
                <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                  {stepState.error}
                </div>
              ) : null}

              {showExecutionLog ? <StepExecutionPanel activity={stepActivity as BuyDecisionStepActivity} /> : null}

              {metrics.length > 0 && !showExecutionLog ? (
                <div className="grid grid-cols-2 gap-2">
                  {metrics.map((metric) => (
                    <div key={`${stepKey}-${metric.label}`} className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                      <p className="text-[11px] uppercase tracking-[0.16em] text-slate-400">{metric.label}</p>
                      <p className="mt-1 text-sm font-medium text-slate-800">{metric.value}</p>
                    </div>
                  ))}
                </div>
              ) : null}

              {expanded && detectorChecklist.length > 0 ? (
                <StepChecklist
                  title={stepKey === 'industry_beta' ? '行业 β 判定器' : '主线属性判定器'}
                  checklist={detectorChecklist}
                />
              ) : null}

              {expanded && !showExecutionLog ? renderStepDetails(stepKey, stepState) : null}

              {expanded && stepState.data && !showExecutionLog ? (
                <details className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
                  <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-medium text-slate-700">
                    原始步骤数据
                    <ChevronDown className="h-4 w-4 text-slate-400" />
                  </summary>
                  <div className="mt-3 overflow-auto rounded-lg border border-slate-200 bg-white p-3">
                    <pre className="whitespace-pre-wrap break-words text-xs leading-5 text-slate-700">{prettyJson(stepState.data)}</pre>
                  </div>
                </details>
              ) : null}

              {stepKey === 'final_decision' && report ? (
                <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={renderDecisionBadgeTone(report.buy_decision.decision)}>{report.buy_decision.decision}</Badge>
                    <Badge variant="warning">{report.buy_decision.entry_type}</Badge>
                    <Badge variant="info">{report.industry_cycle.analysis_status}</Badge>
                  </div>
                  <div className="rounded-xl bg-white px-3 py-3">
                    <p className="text-[11px] uppercase tracking-[0.16em] text-slate-400">现在怎么做</p>
                    <p className="mt-1 text-base font-semibold text-slate-900">{report.final_summary}</p>
                    <p className="mt-2 text-sm leading-6 text-slate-700">{report.buy_decision.decision_reason}</p>
                  </div>
                  <div className="grid gap-2">
                    <div className="rounded-xl bg-white px-3 py-2">
                      <p className="text-[11px] uppercase tracking-[0.16em] text-slate-400">执行方式</p>
                      <div className="mt-2 grid grid-cols-2 gap-2">
                        <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                          <p className="text-[11px] uppercase tracking-[0.16em] text-slate-400">买入判断</p>
                          <p className="mt-1 text-sm font-medium text-slate-900">{report.buy_decision.decision}</p>
                        </div>
                        <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                          <p className="text-[11px] uppercase tracking-[0.16em] text-slate-400">进入方式</p>
                          <p className="mt-1 text-sm font-medium text-slate-900">{report.buy_decision.entry_type}</p>
                        </div>
                      </div>
                    </div>
                    <div className="rounded-xl bg-white px-3 py-2">
                      <p className="text-[11px] uppercase tracking-[0.16em] text-slate-400">为什么不能更激进</p>
                      <div className="mt-1">
                        {renderShortList(report.buy_decision.not_buy_reasons, '当前没有额外买点约束。')}
                      </div>
                    </div>
                    <div className="rounded-xl bg-white px-3 py-2">
                      <p className="text-[11px] uppercase tracking-[0.16em] text-slate-400">接下来盯什么</p>
                      <div className="mt-1">
                        {renderShortList(report.buy_decision.must_watch_points, '当前没有新增观察点。')}
                      </div>
                    </div>
                    <div className="rounded-xl bg-white px-3 py-2">
                      <p className="text-[11px] uppercase tracking-[0.16em] text-slate-400">底层标签</p>
                      <div className="mt-2 grid grid-cols-2 gap-2">
                        <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                          <p className="text-[11px] uppercase tracking-[0.16em] text-slate-400">周期标签</p>
                          <p className="mt-1 text-sm font-medium text-slate-900">{report.industry_cycle.analysis_status}</p>
                        </div>
                        <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                          <p className="text-[11px] uppercase tracking-[0.16em] text-slate-400">景气分</p>
                          <p className="mt-1 text-sm font-medium text-slate-900">{formatMetricValue(report.industry_cycle.prosperity_score)}</p>
                        </div>
                      </div>
                      <p className="mt-2 text-sm leading-6 text-slate-700">{report.industry_cycle.core_logic || report.industry_cycle.prosperity_judgement || '-'}</p>
                    </div>
                    {report.raw_stream_output ? (
                      <details className="rounded-xl bg-white px-3 py-2">
                        <summary className="flex cursor-pointer list-none items-center justify-between text-[11px] font-medium uppercase tracking-[0.16em] text-slate-400">
                          原始模型输出
                          <ChevronDown className="h-4 w-4 normal-case text-slate-400" />
                        </summary>
                        <div className="mt-3 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-3">
                          <pre className="whitespace-pre-wrap break-words text-xs leading-5 text-slate-700">{report.raw_stream_output}</pre>
                        </div>
                      </details>
                    ) : null}
                  </div>
                </div>
              ) : null}

              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  {stepState.from_cache ? <span>缓存</span> : <span>实时</span>}
                  {stepState.blocking ? (
                    <span className="inline-flex items-center gap-1 text-amber-700">
                      <AlertTriangle className="h-3.5 w-3.5" />
                      当前步骤存在阻断
                    </span>
                  ) : null}
                </div>
                <div className="flex items-center gap-2">
                  {stepState.status === 'success' ? (
                    <Button variant="ghost" size="sm" onClick={() => void handleRunStep(stepKey, true)}>
                      <RefreshCw className="h-4 w-4" />
                      重跑
                    </Button>
                  ) : null}
                  <Button
                    variant="outline"
                    size="sm"
                    isLoading={runningStep === stepKey}
                    loadingText="执行中..."
                    disabled={!runAllowed}
                    onClick={() => void handleRunStep(stepKey)}
                  >
                    {stepState.status === 'success' ? (
                      <>
                        <CheckCircle2 className="h-4 w-4" />
                        已完成
                      </>
                    ) : (
                      <>
                        开始本步
                        <ChevronRight className="h-4 w-4" />
                      </>
                    )}
                  </Button>
                </div>
              </div>
            </Card>
          </div>
        );
      })}

      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-slate-200 bg-white/95 p-3 backdrop-blur md:hidden">
        <div className="mx-auto flex max-w-3xl items-center gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-[11px] uppercase tracking-[0.16em] text-slate-400">当前步骤</p>
            <p className="truncate text-sm font-medium text-slate-900">{STEP_META[activeStep].title}</p>
            <p className="mt-0.5 text-xs text-slate-500">{activeStepState?.status === 'success' ? '本步已完成，可重跑' : '完成后才会解锁下一步'}</p>
          </div>
          <Button
            variant={activeStepState?.status === 'success' ? 'ghost' : 'outline'}
            size="sm"
            isLoading={runningStep === activeStep}
            loadingText="执行中..."
            disabled={!activeStepCanRun}
            onClick={() => void handleRunStep(activeStep, activeStepState?.status === 'success')}
          >
            {activeStepState?.status === 'success' ? (
              <>
                <RefreshCw className="h-4 w-4" />
                重跑本步
              </>
            ) : (
              <>
                开始本步
                <ChevronRight className="h-4 w-4" />
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
