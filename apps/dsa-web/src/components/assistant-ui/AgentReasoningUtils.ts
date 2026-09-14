import type { AgentStageEvent } from '../../utils/agentStage';

export type TraceRecord = Record<string, unknown>;

export const isRecord = (value: unknown): value is TraceRecord => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

export const recordsFrom = (value: unknown): TraceRecord[] => (
  Array.isArray(value) ? value.filter(isRecord) : []
);

export const recordValue = (record: TraceRecord | undefined, ...keys: string[]): unknown => {
  if (!record) return undefined;
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return undefined;
};

export const text = (value: unknown, limit = 2_400): string => {
  if (typeof value === 'string') return value.slice(0, limit);
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
};

const id = (value: unknown): string => typeof value === 'string' ? value : '';

export const actionId = (record: TraceRecord | undefined): string => id(
  recordValue(record, 'action_id', 'actionId', 'id'),
);

export const toolName = (record: TraceRecord | undefined): string => text(
  recordValue(record, 'tool_name', 'toolName'),
  120,
);

export const errorCode = (record: TraceRecord | undefined): string => text(
  recordValue(record, 'error_code', 'errorCode'),
  120,
);

/** A failed contract attempt that is expected to be followed by a retry. */
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
  if (event.stage === 'publish') return [];
  const preview = text(recordValue(details, 'answer_preview', 'answerPreview'));
  return preview ? [{ key: 'preview', text: `回答预览：${preview}` }] : [];
};

/**
 * Project only text authored by the planner/executor model.
 *
 * The event summary is an audit fallback for old or tool-silent runs.  New
 * runs put the model's natural-language progress in `progress_text`; this
 * function must not turn structured step fields into conversational prose.
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
  if (progress) return progress;
  if (!isRecoverablePlanningRetry(event)) return '';

  const schema = text(recordValue(details, 'schema', 'contract'), 96);
  if (schema === 'PlanningRoute') return '我正在重新判断这项任务是否需要分阶段核验。';
  if (schema === 'PlanningStepReport') return '刚才的步骤核验结果不够完整，我正在补全后继续。';
  return '刚才的研究计划格式不够完整，我正在修正后继续。';
};

export interface TimelineRow {
  key: string;
  event?: AgentStageEvent;
  result?: TraceRecord;
  kind: 'stage' | 'tool';
  modelTurn?: number;
}

export interface TimelinePhase {
  key: string;
  index: number;
  modelTurn?: number;
  rows: TimelineRow[];
}

export const toolStatus = (result: TraceRecord | undefined, event: AgentStageEvent | undefined): AgentStageEvent['status'] => {
  if (result?.success === false) return 'failed';
  if (event?.status) return event.status;
  if (result?.success === true) return 'completed';
  return 'started';
};

export const toolSummary = (result: TraceRecord | undefined, event: AgentStageEvent | undefined): string => {
  const name = toolName(result)
    || text(recordValue(event?.details, 'tool_name', 'toolName'), 120)
    || event?.summary
    || '原子工具';
  const success = result?.success;
  if (errorCode(result) === 'approval_rejected') return `${name}：用户拒绝，未执行`;
  if (success === false) {
    const errors = Array.isArray(result?.errors)
      ? result.errors.map((value) => text(value)).filter(Boolean).join('；')
      : '';
    return `${name}：${errors || '调用未成功，模型可改用其他来源或收束回答'}`;
  }
  return event?.summary || `${name} 已返回`;
};

export interface ToolOutcomeCounts {
  running: number;
  completed: number;
  failed: number;
}

export const toolOutcomeCounts = (rows: TimelineRow[]): ToolOutcomeCounts => {
  return rows.reduce<ToolOutcomeCounts>((counts, row) => {
    const status = toolStatus(row.result, row.event);
    const problem = status === 'failed'
      || status === 'blocked'
      || status === 'cancelled'
      || Boolean(row.event?.errorCode)
      || Boolean(errorCode(row.result));
    if (status === 'started') counts.running += 1;
    else if (problem) counts.failed += 1;
    else counts.completed += 1;
    return counts;
  }, { running: 0, completed: 0, failed: 0 });
};

const positiveModelTurn = (value: unknown): number | undefined => {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
};

const rowModelTurn = (row: TimelineRow): number | undefined => (
  row.modelTurn ?? positiveModelTurn(recordValue(row.event?.details, 'model_turn', 'modelTurn'))
);

/** Group durable stage and tool events by the model turn that produced them. */
export const groupTimelinePhases = (rows: TimelineRow[]): TimelinePhase[] => {
  const groups = new Map<string, TimelinePhase>();
  let currentKey: string | undefined;
  let currentRunId: string | undefined;
  let legacyIndex = 0;

  for (const row of rows) {
    const event = row.event;
    const eventRunId = event?.runId?.trim();
    if (eventRunId && currentRunId && eventRunId !== currentRunId) currentKey = undefined;
    if (eventRunId) currentRunId = eventRunId;
    const runPrefix = `run:${currentRunId || 'unknown'}`;
    const roundId = event?.roundId?.trim();
    const turn = rowModelTurn(row);
    let key: string;

    if (roundId) {
      key = `${runPrefix}:round:${roundId}`;
    } else if (event?.stage === 'model') {
      key = turn ? `${runPrefix}:turn:${turn}` : `${runPrefix}:legacy:${++legacyIndex}`;
    } else if (turn) {
      key = `${runPrefix}:turn:${turn}`;
    } else {
      key = currentKey || `${runPrefix}:legacy:${++legacyIndex}`;
    }

    currentKey = key;
    const phase = groups.get(key) || {
      key,
      index: groups.size + 1,
      modelTurn: turn,
      rows: [],
    };
    if (!phase.modelTurn && turn) phase.modelTurn = turn;
    phase.rows.push(row);
    groups.set(key, phase);
  }

  return [...groups.values()];
};

const genericToolSummary = (summary: string, name: string): boolean => {
  const normalized = summary.trim();
  return !normalized || normalized === `执行原子工具 ${name}`;
};

export const toolPartLabel = ({
  name,
  summary,
  waitingForApproval,
  problem,
  hasResult,
}: {
  name: string;
  summary: string;
  waitingForApproval: boolean;
  problem: boolean;
  hasResult: boolean;
}): string => {
  if (!genericToolSummary(summary, name)) return summary.trim();
  if (waitingForApproval) return `等待确认：${name}`;
  if (problem) return `${name} 执行失败`;
  if (hasResult) return `${name} 已完成`;
  return `正在执行 ${name}`;
};

const stringValues = (value: unknown, limit = 2_400): string[] => (
  Array.isArray(value) ? value.map((item) => text(item, limit)).filter(Boolean) : []
);

const validUrl = (value: string): string => {
  try {
    const parsed = new URL(value);
    return ['http:', 'https:'].includes(parsed.protocol) ? parsed.toString() : '';
  } catch {
    return '';
  }
};

export const toolDetails = (result: TraceRecord | undefined, event: AgentStageEvent | undefined): DetailLine[] => {
  const details = event?.details || {};
  const lines: DetailLine[] = [];
  const argumentsValue = recordValue(result, 'arguments') ?? recordValue(details, 'arguments');
  const request = argumentSummary(argumentsValue);
  if (request) lines.push({ key: 'request', text: `请求：${request}` });

  const evidenceId = text(recordValue(details, 'evidence_id', 'evidenceId'), 100);
  const dataTime = text(
    recordValue(result, 'data_time', 'dataTime')
    ?? recordValue(details, 'data_time', 'dataTime'),
    160,
  );
  const resultCountValue = recordValue(result, 'result_count', 'resultCount')
    ?? recordValue(details, 'result_count', 'resultCount');
  const resultCount = typeof resultCountValue === 'number' ? resultCountValue : null;
  const omittedValue = recordValue(result, 'omitted_result_count', 'omittedResultCount')
    ?? recordValue(details, 'omitted_result_count', 'omittedResultCount');
  const omitted = typeof omittedValue === 'number' ? omittedValue : 0;
  const sourceLabels = stringValues(
    recordValue(result, 'source_labels', 'sourceLabels')
    ?? recordValue(details, 'source_labels', 'sourceLabels'),
    320,
  );
  const sourceRefs = stringValues(recordValue(result, 'source_refs', 'sourceRefs'), 1_000);
  const resultItems = recordsFrom(
    recordValue(result, 'result_items', 'resultItems')
    ?? recordValue(details, 'result_items', 'resultItems'),
  );
  const referenceLinks = stringValues(
    recordValue(result, 'reference_links', 'referenceLinks')
    ?? recordValue(details, 'reference_links', 'referenceLinks'),
    1_000,
  );

  const urlRefs = [...new Set([...referenceLinks, ...sourceRefs].map(validUrl).filter(Boolean))];
  const urlHosts = new Set(urlRefs.map((url) => {
    try { return new URL(url).hostname; } catch { return ''; }
  }).filter(Boolean));
  const fallbackLabels = sourceRefs.filter((value) => !validUrl(value) && !urlHosts.has(value));
  const labels = [...new Set(sourceLabels.length > 0 ? sourceLabels : fallbackLabels)];
  const overview = [
    resultCount !== null ? `返回 ${resultCount} 条结果` : resultItems.length > 0 ? `展示 ${resultItems.length} 条结果` : '',
    labels.length > 0 ? `数据来源：${labels.join('、')}` : '',
    dataTime ? `数据时间：${dataTime}` : '',
    evidenceId ? `证据：${evidenceId}` : '',
    result?.partial === true ? '部分结果' : '',
    result?.reused === true ? '复用幂等结果' : '',
  ].filter(Boolean).join(' · ');
  if (overview) lines.push({ key: 'overview', text: `结果：${overview}` });

  resultItems.forEach((item, index) => {
    const title = text(recordValue(item, 'title'), 360) || `结果 ${index + 1}`;
    const source = text(recordValue(item, 'source'), 320);
    const publishedAt = text(recordValue(item, 'published_at', 'publishedAt'), 160);
    const summary = text(recordValue(item, 'summary'), 500);
    const attributes = recordsFrom(recordValue(item, 'attributes'))
      .map((attribute) => {
        const name = text(recordValue(attribute, 'name'), 80);
        const value = text(recordValue(attribute, 'value'), 160);
        return name && value ? `${name}=${value}` : '';
      })
      .filter(Boolean);
    const meta = [source, publishedAt, ...attributes, summary ? `摘要：${summary}` : '']
      .filter(Boolean).join(' · ');
    const href = validUrl(text(recordValue(item, 'url'), 1_000));
    lines.push({
      key: `result-${index}`,
      text: `${index + 1}. ${title}${meta ? ` · ${meta}` : ''}`,
      href: href || undefined,
    });
  });

  if (omitted > 0) {
    lines.push({ key: 'omitted', text: `其余 ${omitted} 条结果未放入过程面板，完整原始结果仍保留在运行检查点中。` });
  }
  const itemUrls = new Set(resultItems.map((item) => validUrl(text(recordValue(item, 'url'), 1_000))).filter(Boolean));
  if (resultItems.length === 0) {
    urlRefs.forEach((href, index) => lines.push({ key: `reference-${index}`, text: `引用 ${index + 1}`, href }));
  } else {
    urlRefs.filter((href) => !itemUrls.has(href)).forEach((href, index) => {
      lines.push({ key: `extra-reference-${index}`, text: `补充引用 ${index + 1}`, href });
    });
  }
  const resultSummary = text(
    recordValue(result, 'result_summary', 'resultSummary')
    ?? recordValue(details, 'result_summary', 'resultSummary'),
    800,
  );
  if (resultSummary) lines.push({ key: 'result-summary', text: `返回摘要：${resultSummary}` });
  const errors = stringValues(recordValue(result, 'errors') ?? recordValue(details, 'errors'), 2_400);
  errors.forEach((error, index) => lines.push({ key: `error-${index}`, text: `错误 ${index + 1}：${error}` }));
  return lines;
};

export const phaseStatus = (phase: TimelinePhase): AgentStageEvent['status'] => {
  const statuses = phase.rows.map((row) => {
    const status = row.kind === 'tool' ? toolStatus(row.result, row.event) : row.event?.status || 'started';
    return row.event && isRecoverablePlanningRetry(row.event) ? 'started' : status;
  });
  if (statuses.some((status) => status === 'failed' || status === 'blocked')) return 'failed';
  if (statuses.some((status) => status === 'cancelled')) return 'cancelled';
  if (statuses.some((status) => status === 'started')) return 'started';
  return statuses.at(-1) || 'completed';
};

export const phaseProblem = (phase: TimelinePhase): boolean => phase.rows.some((row) => {
  if (row.event && isRecoverablePlanningRetry(row.event)) return false;
  const status = row.kind === 'tool' ? toolStatus(row.result, row.event) : row.event?.status;
  return status === 'failed'
    || status === 'blocked'
    || status === 'cancelled'
    || Boolean(row.event?.errorCode)
    || Boolean(row.kind === 'tool' && errorCode(row.result));
});

export const phaseHasActiveDetails = (phase: TimelinePhase): boolean => phase.rows.some((row) => {
  if (row.kind === 'tool') return true;
  const stage = row.event?.stage;
  return Boolean(stage && stage !== 'model' && stage !== 'publish');
});

export const phaseHeadline = (phase: TimelinePhase): string => {
  const model = phase.rows.find((row) => row.kind === 'stage' && row.event?.stage === 'model');
  const modelProgress = text(recordValue(model?.event?.details, 'progress_preview'), 360);
  if (modelProgress) return modelProgress;
  if (model?.event?.summary) return model.event.summary;
  const first = phase.rows[0];
  if (first?.kind === 'tool') return toolSummary(first.result, first.event);
  return first?.event?.summary || '执行阶段';
};
