import type { AgentStageEvent } from "../../utils/agentStage";
import { isRecord, recordValue, recordsFrom, text, toolName } from "./AgentReasoningBase";
import type { TraceRecord } from "./AgentReasoningBase";

export const isRecoverablePlanningRetry = (event: AgentStageEvent): boolean => {
  if (event.stage !== 'planning' || event.status !== 'failed') return false;
  const phase = text(recordValue(event.details, 'planning_phase', 'phase'), 64);
  if (phase !== 'contract_retry') return false;
  const attempt = Number(recordValue(event.details, 'attempt'));
  const maxAttempts = Number(recordValue(event.details, 'max_attempts'));
  return !Number.isFinite(maxAttempts) || !Number.isFinite(attempt) || attempt < maxAttempts;
};

export const stageKey = (event: AgentStageEvent): string => [
  event.runId,
  event.stage,
  event.actionId || '',
  event.toolCallId || '',
  event.occurredAt || '',
  event.summary,
].join('|');

export const statusText = (status: AgentStageEvent['status'], problem = false): string => {
  if (problem || status === 'failed' || status === 'blocked') return '有缺口';
  if (status === 'cancelled') return '已取消';
  if (status === 'started') return '进行中';
  return '已完成';
};

export interface DetailLine {
  key: string;
  text: string;
  href?: string;
}

const detailValue = (value: unknown): string => {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (value === null || value === undefined) return '';
  try {
    return JSON.stringify(value, null, 0);
  } catch {
    return String(value);
  }
};

export const argumentSummary = (value: unknown): string => {
  if (!isRecord(value)) return '';
  return Object.entries(value)
    .map(([key, item]) => `${key}=${detailValue(item)}`)
    .join(' · ');
};

export const stageDetails = (event: AgentStageEvent): DetailLine[] => {
  const details = event.details || {};
  if (event.stage === 'model') {
    const operations = recordsFrom(recordValue(details, 'operations'));
    const progress = text(recordValue(details, 'progress_preview'), 2_400);
    return [
      ...(progress ? [{ key: 'progress', text: `过程说明：${progress}` }] : []),
      ...operations.map((operation, index) => {
          const name = toolName(operation) || '原子操作';
          const argumentsText = argumentSummary(recordValue(operation, 'arguments'));
          const argumentKeys = recordValue(operation, 'argument_keys', 'argumentKeys');
          const keys = Array.isArray(argumentKeys)
            ? argumentKeys.map((value) => text(value, 96)).filter(Boolean)
            : [];
          const request = argumentsText || (keys.length > 0 ? `参数字段：${keys.join('、')}` : '无参数');
          return { key: `operation-${index}`, text: `操作 ${index + 1}：${name} · ${request}` };
        }),
    ];
  }
  if (event.stage === 'planning') {
    const phase = text(recordValue(details, 'planning_phase', 'phase'), 64);
    const phaseLabels: Record<string, string> = {
      plan_created: '已生成计划',
      step_started: '开始执行步骤',
      step_completed: '步骤完成',
      goal_checked: '目标检查',
      replanned: '已重新规划',
      finalizing: '最终整理',
    };
    const stepId = text(recordValue(details, 'step_id', 'stepId'), 96);
    const objective = text(recordValue(details, 'objective'), 1_200);
    const goal = text(recordValue(details, 'goal'), 1_600);
    const initialState = text(recordValue(details, 'initial_state', 'initialState'), 800);
    const constraints = Array.isArray(recordValue(details, 'constraints'))
      ? (recordValue(details, 'constraints') as unknown[]).map((value) => text(value, 400)).filter(Boolean)
      : [];
    const completed = text(recordValue(details, 'completed_summary', 'completedSummary'), 1_200);
    const nextReason = text(recordValue(details, 'next_step_reason', 'nextStepReason', 'reason'), 1_200);
    const nextStep = text(recordValue(details, 'next_step_id', 'nextStepId'), 96);
    const criteria = Array.isArray(recordValue(details, 'completion_criteria', 'completionCriteria'))
      ? (recordValue(details, 'completion_criteria', 'completionCriteria') as unknown[])
        .map((value) => text(value, 600)).filter(Boolean)
      : [];
    const criteriaStatus = text(recordValue(details, 'criteria_status', 'criteriaStatus'), 32);
    const criteriaStatusLabel: Record<string, string> = {
      passed: '通过',
      not_met: '未满足',
    };
    const missing = Array.isArray(recordValue(details, 'missing_items', 'missingItems'))
      ? (recordValue(details, 'missing_items', 'missingItems') as unknown[])
        .map((value) => text(value, 600)).filter(Boolean)
      : [];
    const observed = Array.isArray(recordValue(details, 'observed_facts', 'observedFacts'))
      ? (recordValue(details, 'observed_facts', 'observedFacts') as unknown[])
        .map((value) => text(value, 600)).filter(Boolean).slice(0, 3)
      : [];
    const evidenceIds = Array.isArray(recordValue(details, 'evidence_ids', 'evidenceIds'))
      ? (recordValue(details, 'evidence_ids', 'evidenceIds') as unknown[])
        .map((value) => text(value, 100)).filter(Boolean)
      : [];
    const rawSteps = recordsFrom(recordValue(details, 'steps'));
    const steps = rawSteps.map((step, index) => {
      const id = text(recordValue(step, 'step_id', 'stepId'), 96) || `步骤 ${index + 1}`;
      const stepObjective = text(recordValue(step, 'objective'), 240);
      return stepObjective ? `${id}：${stepObjective}` : id;
    });
    const revision = recordValue(details, 'revision');
    return [
      ...(phase ? [{ key: 'planning-phase', text: `阶段：${phaseLabels[phase] || phase}` }] : []),
      ...(stepId ? [{ key: 'planning-step', text: `步骤：${stepId}` }] : []),
      ...(goal ? [{ key: 'planning-goal', text: `计划目标：${goal}` }] : []),
      ...(initialState ? [{ key: 'planning-initial-state', text: `初始状态：${initialState}` }] : []),
      ...(constraints.length > 0
        ? [{ key: 'planning-constraints', text: `约束：${constraints.join('；')}` }]
        : []),
      ...(objective ? [{ key: 'planning-objective', text: `目标：${objective}` }] : []),
      ...(completed ? [{ key: 'planning-completed', text: `已完成：${completed}` }] : []),
      ...(criteria.length > 0
        ? [{ key: 'planning-criteria', text: `完成标准：${criteria.join('；')}` }]
        : []),
      ...(criteriaStatus
        ? [{ key: 'planning-criteria-status', text: `标准检查：${criteriaStatusLabel[criteriaStatus] || criteriaStatus}` }]
        : []),
      ...(steps.length > 0 ? [{ key: 'planning-steps', text: `步骤序列：${steps.join('；')}` }] : []),
      ...(nextStep || nextReason
        ? [{ key: 'planning-next', text: `下一步：${[nextStep, nextReason].filter(Boolean).join(' · ')}` }]
        : []),
      ...missing.map((item, index) => ({ key: `planning-missing-${index}`, text: `缺口：${item}` })),
      ...observed.map((item, index) => ({ key: `planning-observed-${index}`, text: `观察：${item}` })),
      ...(evidenceIds.length > 0
        ? [{ key: 'planning-evidence', text: `关联证据：${evidenceIds.join('、')}` }]
        : []),
      ...(typeof revision === 'number' ? [{ key: 'planning-revision', text: `计划版本：${revision}` }] : []),
    ];
  }
  if (event.stage === 'evidence') {
    const evidenceIds = recordValue(details, 'evidence_ids', 'evidenceIds');
    const ids = Array.isArray(evidenceIds)
      ? evidenceIds.map((value) => text(value, 100)).filter(Boolean).join('、')
      : '';
    const rawIssues = recordValue(details, 'issues');
    const issues = Array.isArray(rawIssues)
      ? rawIssues.map((value) => text(value)).filter(Boolean)
      : '';
    const claimCountValue = recordValue(details, 'claim_count', 'claimCount');
    const factCountValue = recordValue(details, 'fact_claim_count', 'factClaimCount');
    const inferenceCountValue = recordValue(details, 'inference_claim_count', 'inferenceClaimCount');
    const claimCount = typeof claimCountValue === 'number' ? claimCountValue : 0;
    const factCount = typeof factCountValue === 'number' ? factCountValue : 0;
    const inferenceCount = typeof inferenceCountValue === 'number' ? inferenceCountValue : 0;
    const audit = claimCount > 0
      ? `${claimCount} 条结论（事实 ${factCount}、推断 ${inferenceCount}）`
      : '';
    return [
      ...(audit ? [{ key: 'audit', text: `校验范围：${audit}` }] : []),
      ...(ids ? [{ key: 'evidence-ids', text: `已关联证据：${ids}` }] : []),
      ...(Array.isArray(issues)
        ? issues.map((issue, index) => ({ key: `issue-${index}`, text: `缺口 ${index + 1}：${issue}` }))
        : []),
    ];
  }
  if (event.stage === 'approval') {
    const name = text(recordValue(details, 'tool_name', 'toolName'), 120);
    const args = argumentSummary(recordValue(details, 'arguments'));
    return [{ key: 'approval', text: [name, args ? `请求参数：${args}` : ''].filter(Boolean).join(' · ') }];
  }
  if (event.stage === 'reflection') {
    const verdict = text(recordValue(details, 'verdict'), 32);
    const verdictLabel: Record<string, string> = {
      pass: '通过',
      revise: '需要修订',
      block: '阻止发布',
      skip: '已跳过',
    };
    const round = recordValue(details, 'reflection_round', 'reflectionRound');
    const summary = text(recordValue(details, 'summary'), 1_200);
    const reviewerMode = text(recordValue(details, 'reviewer_mode', 'reviewerMode'), 64);
    const rawIssues = recordValue(details, 'issues');
    const issues = Array.isArray(rawIssues)
      ? rawIssues.map((value) => {
        if (!isRecord(value)) return '';
        const block = typeof value.block_index === 'number' ? `第 ${value.block_index} 个区块` : '相关区块';
        const category = text(value.category, 64);
        const severity = text(value.severity, 32);
        const reason = text(value.reason, 600);
        return `${block}${category ? ` · ${category}` : ''}${severity ? ` · ${severity}` : ''}：${reason}`;
      }).filter(Boolean)
      : [];
    return [
      ...(verdict ? [{ key: 'verdict', text: `复核结果：${verdictLabel[verdict] || verdict}` }] : []),
      ...(typeof round === 'number' ? [{ key: 'round', text: `复核轮次：${round}` }] : []),
      ...(reviewerMode ? [{ key: 'reviewer', text: `复核方式：${reviewerMode === 'independent' ? '独立复核' : '受限自复核'}` }] : []),
      ...(summary ? [{ key: 'summary', text: `复核说明：${summary}` }] : []),
      ...issues.map((issue, index) => ({ key: `reflection-issue-${index}`, text: `问题 ${index + 1}：${issue}` })),
    ];
  }
  if (event.stage === 'runtime_error') {
    const receipt = isRecord(recordValue(details, 'runtime_error'))
      ? recordValue(details, 'runtime_error') as TraceRecord
      : details;
    const code = text(recordValue(receipt, 'error_code', 'errorCode'), 128);
    const exceptionType = text(recordValue(receipt, 'exception_type', 'exceptionType'), 160);
    const message = text(recordValue(receipt, 'message'), 1_200);
    const fallbackStatus = text(recordValue(receipt, 'fallback_status', 'fallbackStatus'), 64);
    const retryable = recordValue(receipt, 'retryable') === true;
    return [
      ...(code ? [{ key: 'runtime-error-code', text: `错误码：${code}` }] : []),
      ...(exceptionType ? [{ key: 'runtime-error-type', text: `异常类型：${exceptionType}` }] : []),
      ...(message ? [{ key: 'runtime-error-message', text: `原因：${message}` }] : []),
      ...(retryable ? [{ key: 'runtime-error-retry', text: '处理：已标记为可重试' }] : []),
      ...(fallbackStatus ? [{ key: 'runtime-error-fallback', text: `网页恢复：${fallbackStatus}` }] : []),
    ];
  }
  if (event.stage === 'source_fallback') {
    const fallbackStatus = text(recordValue(details, 'fallback_status', 'fallbackStatus'), 64);
    const requirements = recordsFrom(recordValue(details, 'requirements'));
    return [
      ...(fallbackStatus ? [{ key: 'fallback-status', text: `网页恢复状态：${fallbackStatus}` }] : []),
      ...(requirements.length > 0
        ? [{ key: 'fallback-requirements', text: `待恢复来源：${requirements.map((item) => toolName(item) || text(recordValue(item, 'tool_name', 'toolName'), 120)).filter(Boolean).join('、')}` }]
        : []),
    ];
  }
  if (event.stage === 'publish') return [];
  const preview = text(recordValue(details, 'answer_preview', 'answerPreview'));
  return preview ? [{ key: 'preview', text: `回答预览：${preview}` }] : [];
};

/**
 * Project only text authored by the planner/executor model.
 *
 * Only model-authored progress fields can reach the natural-language view;
 * lifecycle summaries never become conversational prose.
 */
export const planningProgressText = (event: AgentStageEvent): string => {
  if (event.stage !== 'planning') return '';
  const details = event.details || {};
  const progress = text(recordValue(
    details,
    'progress_text',
    'progressText',
    'model_summary',
    'modelSummary',
    'user_message',
    'userMessage',
  ), 1_800).trim();
  return progress;
};

/** Team stages share the Plan projection's natural-language display channel. */
export const isTeamStage = (event: AgentStageEvent): boolean => Boolean(
  recordValue(event.details, 'team_id', 'teamId'),
);

const TEAM_ROLE_LABELS: Record<string, string> = {
  market: '行情',
  fundamental: '基本面',
  news: '新闻',
};

/** Return a safe product label instead of exposing internal worker names. */
export const teamRoleLabel = (event: AgentStageEvent): string => {
  const candidates = [
    text(recordValue(event.details, 'expert_id', 'expertId', 'agent_id', 'agentId'), 160),
    event.actionId || '',
  ].map((value) => value.trim().toLowerCase());
  const matchedRole = Object.keys(TEAM_ROLE_LABELS).find((role) => (
    candidates.some((candidate) => candidate === role || candidate.includes(`:${role}`) || candidate.includes(`${role}_`))
  ));
  return matchedRole ? TEAM_ROLE_LABELS[matchedRole] : '领域';
};

/** Identify the parent lifecycle stage for one domain worker. */
export const isTeamWorkerStage = (event: AgentStageEvent): boolean => (
  isTeamStage(event)
  && event.stage === 'planning'
  && Boolean(text(recordValue(event.details, 'expert_id', 'expertId', 'agent_id', 'agentId'), 160).trim())
  && (event.actionId || '').endsWith(':worker')
);

export const teamProgressText = (event: AgentStageEvent): string => {
  if (!isTeamStage(event)) return '';
  return text(recordValue(
    event.details,
    'progress_text',
    'progressText',
    'user_message',
    'userMessage',
    'model_summary',
    'modelSummary',
  ), 1_800).trim();
};
