import { useCallback, useEffect, useRef, useState } from 'react';
import {
  buyDecisionWorkbenchApi,
  type BuyDecisionReportResponse,
  type BuyDecisionRunStepResponse,
  type BuyDecisionStepKey,
  type BuyDecisionStepState,
  type BuyDecisionWorkbenchResponse,
} from '../api/buyDecisionWorkbench';

const STORAGE_PREFIX = 'buy-decision-workbench:session:';

const STEP_ORDER: BuyDecisionStepKey[] = [
  'profile_mapping',
  'industry_beta',
  'mainline_position',
  'company_benefit',
  'buy_constraints',
  'final_decision',
];

export interface BuyDecisionStepLogEntry {
  id: string;
  at: string;
  kind: 'system' | 'query' | 'data' | 'success' | 'error';
  message: string;
  detail?: string;
  source?: string;
  analysis?: string;
  raw?: unknown;
}

export interface BuyDecisionStepActivity {
  stepKey: BuyDecisionStepKey;
  status: 'running' | 'success' | 'failed';
  startedAt: string;
  finishedAt?: string;
  force: boolean;
  staleSummary?: string | null;
  staleData?: unknown;
  logs: BuyDecisionStepLogEntry[];
}

const STEP_PROGRESS_TEMPLATES: Record<BuyDecisionStepKey, Array<{ kind: BuyDecisionStepLogEntry['kind']; message: string; detail: string }>> = {
  profile_mapping: [
    { kind: 'query', message: '读取股票基础资料', detail: '获取名称、行业、主营和交易所信息。' },
    { kind: 'query', message: '校验行业映射', detail: '检查主营和行业主题的对应关系与映射强度。' },
    { kind: 'system', message: '整理基础结论', detail: '输出本步摘要，准备解锁行业 β 研判。' },
  ],
  industry_beta: [
    { kind: 'query', message: '拉取行业 beta 证据', detail: '读取板块涨跌、排名、资金流和空间判断。' },
    { kind: 'query', message: '校验价格战与驱动', detail: '确认价格战风险、需求和政策驱动信号。' },
    { kind: 'system', message: '生成行业 β 结论', detail: '整理 detector 清单和本步摘要。' },
  ],
  mainline_position: [
    { kind: 'query', message: '拉取主线位置数据', detail: '读取市场主线、分支主线和热度快照。' },
    { kind: 'query', message: '对齐主题与催化', detail: '核对主题匹配、概念纯度和催化持续性。' },
    { kind: 'system', message: '生成市场位置结论', detail: '整理主线属性判定器和本步摘要。' },
  ],
  company_benefit: [
    { kind: 'query', message: '收集个股受益证据', detail: '读取主营受益、财务、估值、股东和交易快照。' },
    { kind: 'query', message: '补足情绪与风险', detail: '汇总研报、讨论度、社交情绪和风险标签。' },
    { kind: 'system', message: '生成受益与弹性结论', detail: '整理个股证据点、弹性信号和本步摘要。' },
  ],
  buy_constraints: [
    { kind: 'query', message: '收集买点约束数据', detail: '读取追高、位置、估值和事件风险约束。' },
    { kind: 'query', message: '整理观察点', detail: '汇总情绪分、社交分、风险标签和观察条件。' },
    { kind: 'system', message: '生成风险约束结论', detail: '输出本步摘要，准备最终决策。' },
  ],
  final_decision: [
    { kind: 'query', message: '汇总完整证据包', detail: '整合前五步的行业、主线、个股和风险证据。' },
    { kind: 'query', message: '统一决策归纳', detail: '校验周期标签、买点约束和进入方式。' },
    { kind: 'success', message: '输出最终结论', detail: '生成买入判断、关键理由和观察点。' },
  ],
};

function createLogEntry(
  kind: BuyDecisionStepLogEntry['kind'],
  message: string,
  detail?: string,
  options?: Pick<BuyDecisionStepLogEntry, 'source' | 'analysis' | 'raw'>,
): BuyDecisionStepLogEntry {
  const at = new Date().toISOString();
  return {
    id: `${at}-${Math.random().toString(36).slice(2, 8)}`,
    at,
    kind,
    message,
    detail,
    source: options?.source,
    analysis: options?.analysis,
    raw: options?.raw,
  };
}

function getValue(value: unknown, fallback: string = '-'): string {
  if (value === null || value === undefined || value === '') return fallback;
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2);
  return String(value);
}

function hasValue(value: unknown): boolean {
  return !(value === null || value === undefined || value === '');
}

function getListText(value: unknown, fallback: string = '-') {
  if (!Array.isArray(value)) return fallback;
  const items = value.map((item) => String(item).trim()).filter(Boolean);
  return items.length ? items.join(' / ') : fallback;
}

function getReasonText(value: unknown, fallback: string = '-') {
  if (Array.isArray(value)) {
    const items = value.map((item) => String(item).trim()).filter(Boolean);
    return items.length ? items.join('；') : fallback;
  }
  return getValue(value, fallback);
}

function buildResultLogs(stepKey: BuyDecisionStepKey, result: BuyDecisionRunStepResponse<unknown>): BuyDecisionStepLogEntry[] {
  const data = (result.data ?? {}) as Record<string, unknown>;

  if (stepKey === 'profile_mapping') {
    const profile = (data.stock_profile as Record<string, unknown> | undefined) ?? {};
    const mapping = (data.industry_mapping as Record<string, unknown> | undefined) ?? {};
    return [
      createLogEntry('data', `已获取股票基础资料: ${getValue(profile.stock_name)} / ${getValue(profile.industry_name)}`, `主营 ${getValue(profile.main_business)}，交易所 ${getValue(profile.exchange)}`),
      createLogEntry('data', `行业映射强度 ${getValue(mapping.industry_match_level)}`, `主营词 ${getListText(mapping.business_keywords)}，说明 ${getValue(mapping.mapping_reason)}`, {
        source: '基础资料服务 / 行业映射服务',
        analysis: `主营描述与所属行业的映射强度为 ${getValue(mapping.industry_match_level)}，后续步骤默认沿用该行业归属。`,
        raw: {
          stock_profile: profile,
          industry_mapping: mapping,
        },
      }),
      createLogEntry('success', `本步完成: ${getValue(result.summary, '基础资料和行业映射已整理完成')}`, undefined, {
        source: '步骤汇总',
        analysis: '基础资料校验通过，后续行业与主线证据可以继续建立在当前映射上。',
      }),
    ];
  }

  if (stepKey === 'industry_beta') {
    const evidence = (data.industry_beta_evidence as Record<string, unknown> | undefined) ?? {};
    const sector = (evidence.sector_snapshot as Record<string, unknown> | undefined) ?? {};
    const flow = (evidence.fund_flow as Record<string, unknown> | undefined) ?? {};
    const peer = (evidence.peer_group as Record<string, unknown> | undefined) ?? {};
    const war = (evidence.price_war_signal as Record<string, unknown> | undefined) ?? {};
    const space = (evidence.industry_space as Record<string, unknown> | undefined) ?? {};
    const prosperity = (evidence.prosperity_snapshot as Record<string, unknown> | undefined) ?? {};
    const detector = (data.industry_beta_detector as Record<string, unknown> | undefined) ?? {};
    const quality = (data.data_quality as Record<string, unknown> | undefined) ?? {};
    const sectorRankReady = hasValue(sector.rank) && hasValue(sector.total);
    const sectorChangeReady = hasValue(sector.change_pct);
    const flowRankReady = hasValue(flow.rank) && hasValue(flow.total);
    const flowNetReady = hasValue(flow.net_inflow);
    const flowAmountReady = hasValue(flow.total_amount);
    const hasBoardEvidence = Boolean(quality.has_sector_snapshot) || sectorRankReady || sectorChangeReady;
    const hasFlowEvidence = Boolean(quality.has_fund_flow) || flowRankReady || flowNetReady || flowAmountReady;
    const hasPeerEvidence = Boolean(quality.has_peer_group);
    const hasDriverEvidence = Boolean(quality.has_driver_signals);
    const missingFields = Array.isArray(quality.missing_fields) ? quality.missing_fields.map(String).filter(Boolean) : [];
    const driverSignals = (evidence.driver_signals as Record<string, unknown> | undefined) ?? {};
    const driverSummary = Object.entries(driverSignals)
      .filter(([, value]) => Array.isArray(value) && value.length > 0)
      .map(([key, value]) => `${key}:${(value as unknown[]).map((item) => String(item)).join('、')}`);
    return [
      createLogEntry(
        'data',
        hasValue(prosperity.judgement)
          ? `行业景气主判断已整理: ${getValue(prosperity.judgement)}`
          : '行业景气主判断待补充: 当前先展示底层证据，不提前输出景气方向结论',
        '本步先收集景气、空间、价格战和驱动四组证据，板块快照只是一项辅助信号。',
        {
          source: '行业景气主判断',
          raw: {
            prosperity_snapshot: prosperity,
            data_quality: quality,
          },
        }),
      createLogEntry(
        'data',
        hasBoardEvidence || hasFlowEvidence
          ? `行业景气因子已获取: 板块 ${getValue(sector.name)}，涨跌 ${getValue(sector.change_pct)}%，资金活跃度已记录`
          : '行业景气因子缺失: 板块景气和资金活跃度字段当前都未取到',
        hasFlowEvidence
          ? `板块来源 ${getValue(sector.source, getValue(quality.sector_source))}；资金来源 ${getValue(flow.source, getValue(quality.fund_flow_source))}；资金排名 ${getValue(flow.rank)}/${getValue(flow.total)}；净流入 ${getValue(flow.net_inflow)}；成交额 ${getValue(flow.total_amount)}`
          : `板块来源 ${getValue(sector.source, getValue(quality.sector_source))}`,
        {
          source: '行业景气因子',
          analysis: hasBoardEvidence || hasFlowEvidence
            ? '这里落的是景气侧底层快照。板块涨跌、活跃度和资金排序只是景气因子，不直接等于行业 β 结论。'
            : '当前景气快照不完整，这一轮只能保留弱证据，不能单凭缺失数据输出方向判断。',
          raw: {
            sector_snapshot: sector,
            fund_flow: flow,
          },
        }),
      createLogEntry(
        'data',
        hasPeerEvidence
          ? `行业空间因子已获取: 同行样本 ${getValue(peer.sample_size)} 个，空间判断 ${getValue(space.space_level)}`
          : `行业空间因子已获取: 空间判断 ${getValue(space.space_level)}，但同行对照样本不足`,
        `空间说明 ${getReasonText(space.reason)}；同行样本 ${getListText(peer.sample_names)}`,
        {
          source: '行业空间因子',
          raw: {
            industry_space: space,
            peer_group: peer,
          },
        }),
      createLogEntry(
        'data',
        hasValue(war.level) || hasDriverEvidence
          ? `价格战与驱动因子已整理: 价格战 ${getValue(war.level)}，驱动 ${driverSummary.length ? '已获取' : '不足'}`
          : '价格战与驱动因子缺失: 当前没有足够字段支持竞争格局与产业驱动判断',
        driverSummary.length ? `驱动线索 ${driverSummary.join(' / ')}` : (missingFields.length ? `缺失字段 ${missingFields.join(' / ')}` : '驱动线索暂未聚合出来'),
        {
          source: '价格战 / 驱动因子',
          raw: {
            price_war_signal: war,
            driver_signals: driverSignals,
            data_quality: quality,
          },
        }),
      createLogEntry(
        'success',
        `行业 β 判定 ${Boolean(detector.passed) ? '通过' : '未完全通过'}`,
        getValue(result.summary, '行业 β 证据已整理完成'),
        {
          source: '判定器汇总',
          analysis: missingFields.length > 0
            ? `当前仍缺少 ${missingFields.join(' / ')}，所以这一步先完成四组因子取证，行业 β 只能保留弱结论。`
            : detector.conclusion
              ? String(detector.conclusion)
              : '行业 β 已在景气、空间、价格战和驱动四组因子收齐后完成归纳。',
          raw: detector,
        }),
    ];
  }

  if (stepKey === 'mainline_position') {
    const evidence = (data.mainline_evidence as Record<string, unknown> | undefined) ?? {};
    const market = (evidence.market_mainline as Record<string, unknown> | undefined) ?? {};
    const heat = (evidence.heat_snapshot as Record<string, unknown> | undefined) ?? {};
    const concept = (evidence.concept_purity as Record<string, unknown> | undefined) ?? {};
    const catalyst = (evidence.catalyst_snapshot as Record<string, unknown> | undefined) ?? {};
    const detector = (data.mainline_detector as Record<string, unknown> | undefined) ?? {};
    return [
      createLogEntry('data', `主线位置: ${getValue(market.current_status)}，主题 ${getValue(market.matched_theme)}`, `热度 ${getValue(heat.heat_level)}，讨论数 ${getValue(heat.discussion_count)}`, {
        source: '市场主线服务 / 热度快照',
        analysis: `是否具备主线属性，先看当前市场状态 ${getValue(market.current_status)} 与主题匹配 ${getValue(market.matched_theme)}。`,
        raw: {
          market_mainline: market,
          heat_snapshot: heat,
        },
      }),
      createLogEntry('data', `概念纯度 ${getValue(concept.level)}，催化 ${getValue(catalyst.count)} 个`, `催化项 ${getListText(catalyst.items)}`, {
        source: '概念纯度 / 催化事件服务',
        analysis: `主线持续性由概念纯度 ${getValue(concept.level)} 和催化持续性共同决定。`,
        raw: {
          concept_purity: concept,
          catalyst_snapshot: catalyst,
          detector,
        },
      }),
      createLogEntry('success', `主线属性判定 ${Boolean(detector.passed) ? '通过' : '未完全通过'}`, getValue(result.summary, '市场位置证据已整理完成'), {
        source: '判定器汇总',
        analysis: detector.conclusion ? String(detector.conclusion) : '已根据市场位置、主题和催化完成主线属性归纳。',
        raw: detector,
      }),
    ];
  }

  if (stepKey === 'company_benefit') {
    const evidence = (data.company_evidence as Record<string, unknown> | undefined) ?? {};
    const focus = (evidence.stock_focus_snapshot as Record<string, unknown> | undefined) ?? {};
    const financial = (evidence.financial_snapshot as Record<string, unknown> | undefined) ?? {};
    const valuation = (evidence.valuation_snapshot as Record<string, unknown> | undefined) ?? {};
    const sentiment = (evidence.sentiment_snapshot as Record<string, unknown> | undefined) ?? {};
    return [
      createLogEntry('data', `受益对齐 ${getValue(evidence.benefit_alignment)}，业务绑定 ${getValue(focus.business_binding_strength)}`, `财务 ${getValue(focus.finance_state)}，股东 ${getValue(focus.holder_state)}，交易 ${getValue(focus.trading_state)}`, {
        source: '受益证据服务 / 股东与交易快照',
        analysis: `主营受益不是看概念，而是看业务绑定强度 ${getValue(focus.business_binding_strength)} 和直接受益证据。`,
        raw: {
          stock_focus_snapshot: focus,
          benefit_alignment: evidence.benefit_alignment,
        },
      }),
      createLogEntry('data', `财务与估值: 营收增速 ${getValue(financial.revenue_yoy)}，利润增速 ${getValue(financial.profit_yoy)}，PE ${getValue(valuation.pe_ttm)}`, `透支状态 ${getValue(valuation.price_overdraft_status ?? valuation.valuation_status)}`, {
        source: '财务快照 / 估值快照',
        analysis: `个股弹性既看业绩增速，也看估值是否已经透支。`,
        raw: {
          financial_snapshot: financial,
          valuation_snapshot: valuation,
        },
      }),
      createLogEntry('data', `情绪快照: 研报 ${getValue(sentiment.research_count)}，正向研报 ${getValue(sentiment.positive_research_count)}，讨论度 ${getValue(sentiment.discussion_count)}`, undefined, {
        source: '情绪与舆情服务',
        analysis: '情绪不是最终结论，但会影响买点的右侧确认与追高风险。',
        raw: {
          sentiment_snapshot: sentiment,
          risk_snapshot: evidence.risk_snapshot,
        },
      }),
      createLogEntry('success', `本步完成: ${getValue(result.summary, '个股受益与弹性证据已整理完成')}`, undefined, {
        source: '步骤汇总',
        analysis: '已形成主营受益、财务估值和市场情绪的综合证据包。',
      }),
    ];
  }

  if (stepKey === 'buy_constraints') {
    const constraints = (data.buy_constraints as Record<string, unknown> | undefined) ?? {};
    const risk = (constraints.risk_snapshot as Record<string, unknown> | undefined) ?? {};
    const sentiment = (constraints.sentiment_snapshot as Record<string, unknown> | undefined) ?? {};
    return [
      createLogEntry('data', `买点约束: 追高 ${getValue(constraints.chasing_risk)}，位置 ${getValue(constraints.position_risk)}，估值 ${getValue(constraints.valuation_risk)}`, `事件风险 ${getValue(constraints.event_risk)}`, {
        source: '约束判断服务',
        analysis: '这一层不下最终结论，只负责把不能贸然买入的约束条件抽出来。',
        raw: {
          buy_constraints: constraints,
        },
      }),
      createLogEntry('data', `风险与情绪: 高风险 ${getValue(risk.high_risk_count)}，中风险 ${getValue(risk.medium_risk_count)}`, `情绪分 ${getValue(sentiment.sentiment_score)}，社交分 ${getValue(sentiment.social_score)}`, {
        source: '风险事件 / 情绪快照',
        analysis: '高风险事件、情绪分和社交分会直接影响买点时机与追高禁忌。',
        raw: {
          risk_snapshot: risk,
          sentiment_snapshot: sentiment,
        },
      }),
      createLogEntry('success', `本步完成: ${getValue(result.summary, '风险与买点约束已整理完成')}`, undefined, {
        source: '步骤汇总',
        analysis: '已输出本步的约束条件和观察点，供最终决策收口。',
      }),
    ];
  }

  if (stepKey === 'final_decision') {
    const decision = (data.buy_decision as Record<string, unknown> | undefined) ?? {};
    const cycle = (data.industry_cycle as Record<string, unknown> | undefined) ?? {};
    return [
      createLogEntry('data', `统一汇总完成: 买入判断 ${getValue(decision.decision)}，进入方式 ${getValue(decision.entry_type)}`, `周期标签 ${getValue(cycle.analysis_status)}，景气分 ${getValue(cycle.prosperity_score)}`, {
        source: '最终决策汇总器',
        analysis: '最终结论不是单看主线或行业 beta，而是综合前五步证据后统一归纳。',
        raw: {
          buy_decision: decision,
          industry_cycle: cycle,
        },
      }),
      createLogEntry('data', `关键理由: ${getValue(decision.decision_reason)}`, `不买原因 ${getListText(decision.not_buy_reasons)}；观察点 ${getListText(decision.must_watch_points)}`, {
        source: 'LLM / 规则归纳输出',
        analysis: '这里展示最终研判的理由、约束和后续观察点。',
        raw: {
          decision_reason: decision.decision_reason,
          not_buy_reasons: decision.not_buy_reasons,
          must_watch_points: decision.must_watch_points,
        },
      }),
      createLogEntry('success', `最终结论已生成: ${getValue(result.summary, '买入结论已整理完成')}`, undefined, {
        source: '最终报告',
        analysis: '结论已可用于页面最终展示和买入判断输出。',
      }),
    ];
  }

  return [createLogEntry('success', `本步完成: ${getValue(result.summary, '执行完成')}`)];
}

function buildStateLogs(
  stepKey: BuyDecisionStepKey,
  stepState: BuyDecisionStepState | undefined,
): BuyDecisionStepLogEntry[] {
  if (!stepState?.data) return [];
  return buildResultLogs(stepKey, {
    session_id: '',
    step: stepKey,
    status: stepState.status,
    from_cache: stepState.from_cache,
    started_at: stepState.started_at,
    finished_at: stepState.finished_at,
    summary: stepState.summary,
    error: stepState.error,
    blocking: stepState.blocking,
    next_step_enabled: stepState.next_step_enabled,
    data: stepState.data,
  });
}

function buildActivitiesFromWorkbench(
  workbench: BuyDecisionWorkbenchResponse,
): Partial<Record<BuyDecisionStepKey, BuyDecisionStepActivity>> {
  const next: Partial<Record<BuyDecisionStepKey, BuyDecisionStepActivity>> = {};

  STEP_ORDER.forEach((stepKey) => {
    const stepState = workbench.steps[stepKey];
    if (stepState?.status !== 'success' || !stepState.data) return;
    next[stepKey] = {
      stepKey,
      status: 'success',
      startedAt: stepState.started_at ?? new Date().toISOString(),
      finishedAt: stepState.finished_at ?? stepState.started_at ?? new Date().toISOString(),
      force: false,
      staleSummary: null,
      staleData: undefined,
      logs: buildStateLogs(stepKey, stepState),
    };
  });

  return next;
}

export interface UseBuyDecisionWorkbenchResult {
  state: BuyDecisionWorkbenchResponse | null;
  report: BuyDecisionReportResponse | null;
  initializing: boolean;
  runningStep: BuyDecisionStepKey | null;
  stepActivities: Partial<Record<BuyDecisionStepKey, BuyDecisionStepActivity>>;
  error: string | null;
  initialize: (symbol: string, forceReset?: boolean) => Promise<void>;
  refresh: () => Promise<void>;
  runStep: <T = unknown>(stepKey: BuyDecisionStepKey, force?: boolean) => Promise<BuyDecisionRunStepResponse<T> | null>;
  loadReport: () => Promise<void>;
  reset: () => void;
}

export function useBuyDecisionWorkbench(): UseBuyDecisionWorkbenchResult {
  const [state, setState] = useState<BuyDecisionWorkbenchResponse | null>(null);
  const [report, setReport] = useState<BuyDecisionReportResponse | null>(null);
  const [initializing, setInitializing] = useState(false);
  const [runningStep, setRunningStep] = useState<BuyDecisionStepKey | null>(null);
  const [stepActivities, setStepActivities] = useState<Partial<Record<BuyDecisionStepKey, BuyDecisionStepActivity>>>({});
  const [error, setError] = useState<string | null>(null);
  const progressTimerRef = useRef<number | null>(null);

  const clearProgressTimer = useCallback(() => {
    if (progressTimerRef.current !== null && typeof window !== 'undefined') {
      window.clearInterval(progressTimerRef.current);
    }
    progressTimerRef.current = null;
  }, []);

  useEffect(() => () => clearProgressTimer(), [clearProgressTimer]);

  const buildActivity = useCallback((
    stepKey: BuyDecisionStepKey,
    force: boolean,
    previousStep: BuyDecisionStepState | undefined,
  ): BuyDecisionStepActivity => ({
    stepKey,
    status: 'running',
    startedAt: new Date().toISOString(),
    force,
    staleSummary: previousStep?.summary ?? null,
    staleData: previousStep?.data,
    logs: [
      createLogEntry(
        'system',
        force ? '开始重跑本步' : '开始执行本步',
        force ? '这是一次重跑。上一轮结果会保留用于对照，下面开始记录本次新的执行过程。' : '将按顺序记录取数、整理证据和生成结论的过程。',
      ),
    ],
  }), []);

  const startProgressPlayback = useCallback((stepKey: BuyDecisionStepKey) => {
    clearProgressTimer();
    if (typeof window === 'undefined') return;

    let nextIndex = 0;

    progressTimerRef.current = window.setInterval(() => {
      setStepActivities((current) => {
        const activity = current[stepKey];
        if (!activity || activity.status !== 'running') {
          clearProgressTimer();
          return current;
        }

        const nextTemplate = STEP_PROGRESS_TEMPLATES[stepKey][nextIndex];
        if (!nextTemplate) {
          clearProgressTimer();
          return current;
        }

        nextIndex += 1;

        return {
          ...current,
          [stepKey]: {
            ...activity,
            logs: [
              ...activity.logs,
              createLogEntry(nextTemplate.kind, nextTemplate.message, nextTemplate.detail),
            ],
          },
        };
      });
    }, 900);
  }, [clearProgressTimer]);

  const persistSession = useCallback((symbol: string, sessionId: string | null) => {
    if (typeof window === 'undefined') return;
    const key = `${STORAGE_PREFIX}${symbol}`;
    if (sessionId) {
      window.localStorage.setItem(key, sessionId);
    } else {
      window.localStorage.removeItem(key);
    }
  }, []);

  const initialize = useCallback(async (symbol: string, forceReset: boolean = false) => {
    setInitializing(true);
    setError(null);
    try {
      if (!forceReset && typeof window !== 'undefined') {
        const rememberedSessionId = window.localStorage.getItem(`${STORAGE_PREFIX}${symbol}`);
        if (rememberedSessionId) {
          try {
            const restored = await buyDecisionWorkbenchApi.getWorkbench(rememberedSessionId);
            setState(restored);
            setReport(null);
            setStepActivities(buildActivitiesFromWorkbench(restored));
            return;
          } catch {
            // Session expired or server restarted — clear and fall through to create new
            persistSession(symbol, null);
          }
        }
      }
      // Create fresh session (either no saved session, force reset, or restore failed)
      const next = await buyDecisionWorkbenchApi.createWorkbench(symbol, forceReset);
      setState(next);
      setReport(null);
      setStepActivities(buildActivitiesFromWorkbench(next));
      persistSession(symbol, next.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : '初始化买入判断工作台失败');
      throw err;
    } finally {
      setInitializing(false);
    }
  }, [persistSession]);

  const refresh = useCallback(async () => {
    if (!state?.session_id) return;
    setError(null);
    try {
      const next = await buyDecisionWorkbenchApi.getWorkbench(state.session_id);
      setState(next);
      setStepActivities(buildActivitiesFromWorkbench(next));
      persistSession(next.symbol, next.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : '刷新买入判断工作台失败');
      throw err;
    }
  }, [persistSession, state?.session_id]);

  const runStep = useCallback(async <T = unknown>(stepKey: BuyDecisionStepKey, force: boolean = false) => {
    if (!state?.session_id) return null;
    const previousStep = state.steps[stepKey];
    const activity = buildActivity(stepKey, force, previousStep);

    setRunningStep(stepKey);
    setError(null);
    setReport(null);
    setStepActivities((current) => ({
      ...current,
      [stepKey]: activity,
    }));
    setState((current) => {
      if (!current) return current;
      return {
        ...current,
        current_step: stepKey,
        steps: {
          ...current.steps,
          [stepKey]: {
            ...current.steps[stepKey],
            status: 'running',
            from_cache: false,
            started_at: activity.startedAt,
            finished_at: null,
            summary: force ? '正在重新执行本步，下面会展示本步的取数和整理过程。' : '正在执行本步，下面会展示本步的取数和整理过程。',
            error: null,
            blocking: false,
            next_step_enabled: false,
            data: undefined,
          },
        },
      };
    });
    startProgressPlayback(stepKey);

    try {
      const result = await buyDecisionWorkbenchApi.runStep<T>(state.session_id, stepKey, force);
      clearProgressTimer();
      setStepActivities((current) => ({
        ...current,
        [stepKey]: {
          ...(current[stepKey] ?? activity),
          status: 'success',
          finishedAt: result.finished_at ?? new Date().toISOString(),
          logs: [
            ...((current[stepKey]?.logs ?? activity.logs)),
            ...buildResultLogs(stepKey, result),
          ],
        },
      }));
      setState((current) => {
        if (!current) return current;
        const stepIndex = STEP_ORDER.indexOf(stepKey);
        const nextCurrentStep = stepIndex >= 0 && stepIndex + 1 < STEP_ORDER.length
          ? STEP_ORDER[stepIndex + 1]
          : stepKey;
        return {
          ...current,
          current_step: nextCurrentStep,
          steps: {
            ...current.steps,
            [stepKey]: {
              status: result.status,
              from_cache: result.from_cache,
              started_at: result.started_at,
              finished_at: result.finished_at,
              summary: result.summary,
              error: result.error,
              blocking: result.blocking,
              next_step_enabled: result.next_step_enabled,
              data: result.data,
            },
          },
        };
      });
      return result;
    } catch (err) {
      clearProgressTimer();
      const message = err instanceof Error ? err.message : '执行买入判断步骤失败';
      setStepActivities((current) => ({
        ...current,
        [stepKey]: {
          ...(current[stepKey] ?? activity),
          status: 'failed',
          finishedAt: new Date().toISOString(),
          logs: [
            ...((current[stepKey]?.logs ?? activity.logs)),
            createLogEntry('error', '本步执行失败', message),
          ],
        },
      }));
      setState((current) => {
        if (!current) return current;
        return {
          ...current,
          current_step: stepKey,
          steps: {
            ...current.steps,
            [stepKey]: {
              ...current.steps[stepKey],
              status: 'failed',
              finished_at: new Date().toISOString(),
              summary: activity.force ? '本步重跑失败，请查看错误并再次执行。' : '本步执行失败，请查看错误并再次执行。',
              error: message,
              blocking: true,
              next_step_enabled: false,
            },
          },
        };
      });
      setError(message);
      throw err;
    } finally {
      setRunningStep(null);
    }
  }, [buildActivity, clearProgressTimer, startProgressPlayback, state]);

  const loadReport = useCallback(async () => {
    if (!state?.session_id) return;
    setError(null);
    try {
      const next = await buyDecisionWorkbenchApi.getReport(state.session_id);
    setReport(next);
    persistSession(next.symbol, next.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载买入结论失败');
      throw err;
    }
  }, [persistSession, state?.session_id]);

  const reset = useCallback(() => {
    if (state?.symbol) {
      persistSession(state.symbol, null);
    }
    setState(null);
    setReport(null);
    setStepActivities({});
    setInitializing(false);
    setRunningStep(null);
    setError(null);
    clearProgressTimer();
  }, [clearProgressTimer, persistSession, state?.symbol]);

  return {
    state,
    report,
    initializing,
    runningStep,
    stepActivities,
    error,
    initialize,
    refresh,
    runStep,
    loadReport,
    reset,
  };
}

export default useBuyDecisionWorkbench;
