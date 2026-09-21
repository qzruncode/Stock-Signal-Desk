import type { AgentExecutionTrace } from '../../api/agent';
import { runtimeErrorId, teamRuntimeErrors } from './TeamRuntimeErrors';
import { agentStageEvents, reconcileTerminalStageEvents, type AgentStageEvent } from '../../utils/agentStage';
import { isRecord, recordValue, teamProgressText, type TraceRecord } from './AgentReasoningUtils';
import { type TeamRole, terminalStatuses, type TeamPartRecord, type TeamToolRecord, type TeamChartRecord, type TeamReviewPhase, type TeamReviewReport, type TeamMemberModel, type TeamMemberFlowItem, type TeamBoardModel, type TeamWorkerProgressAnchor, type TeamModelProjection, ROLE_ORDER, isWorkerRole, normalizeText, normalizedRole, roleLabel, statusValue, stageOutcomeStatus, partToolId, partToolName, partIdentityValues, identitiesMatch, eventRole, eventTaskId, eventAgentId, findToolEvent, findChartEvent, findRuntimeErrorEvent, teamTrace, taskRecords, resultRecords, stageEventsFromParts, orderedStageEvents, reviewPhase, memberStatus, terminalMemberStatus, memberProgress, memberSummary, uniqueProgress } from './TeamBoardProjectionState';
export type { TeamRole, TeamMemberStatus, TeamPartRecord, TeamToolRecord, TeamChartRecord, TeamReviewPhase, TeamReviewReport, TeamFailureModel, TeamMemberModel, TeamMemberFlowItem, TeamBoardModel, TeamWorkerProgressAnchor, TeamModelProjection } from './TeamBoardProjectionState';
export const teamRoleLabel = (role: TeamRole): string => roleLabel(role);

export const teamModelProjectionFrom = (value: unknown): Omit<TeamModelProjection, 'partId' | 'contentIndex'> | undefined => {
  if (!isRecord(value)) return undefined;
  const text = normalizeText(recordValue(value, 'text'), 1_800);
  const projectionSource = normalizeText(recordValue(value, 'projection_source', 'projectionSource'), 32);
  if (!text || projectionSource !== 'model') return undefined;
  const numberValue = Number(recordValue(value, 'sequence'));
  return {
    text,
    projectionSource: 'model',
    scope: normalizeText(recordValue(value, 'scope'), 32) || 'coordinator',
    namespace: normalizeText(recordValue(value, 'namespace'), 192),
    agentId: normalizeText(recordValue(value, 'agent_id', 'agentId'), 96),
    taskId: normalizeText(recordValue(value, 'task_id', 'taskId'), 96),
    phase: normalizeText(recordValue(value, 'phase'), 64),
    kind: normalizeText(recordValue(value, 'kind'), 64) || 'progress',
    sequence: Number.isFinite(numberValue) ? numberValue : 0,
  };
};

export const teamModelProjectionsFromParts = (
  parts: readonly unknown[],
): TeamModelProjection[] => {
  type StoredProjection = {
    projection: TeamModelProjection;
    firstContentIndex: number;
  };
  const latest = new Map<string, StoredProjection>();
  parts.forEach((value, contentIndex) => {
    if (!isRecord(value) || value.type !== 'data' || normalizeText(value.name, 96) !== 'team-model-projection') {
      return;
    }
    const projection = teamModelProjectionFrom(value.data);
    if (!projection) return;
    const projectionData = isRecord(value.data) ? value.data : undefined;
    const partId = normalizeText(recordValue(value, 'part_id', 'partId'), 192)
      || normalizeText(recordValue(projectionData, 'projection_id', 'projectionId'), 192);
    if (!partId) return;
    const existing = latest.get(partId);
    const isNewer = !existing
      || projection.sequence >= existing.projection.sequence
      || projection.sequence === 0;
    // The streaming contract callback and the accepted structured value can
    // share one projection id.  The accepted value is sometimes only a
    // compact summary, while the user has already seen the richer model
    // projection.  Keep updates for one semantic projection monotonic: a
    // later shorter value must not erase visible model output.  A genuinely
    // new semantic turn must receive a new projection_id.
    const doesNotShortenVisibleText = !existing
      || projection.text.length >= existing.projection.text.length;
    if (isNewer && doesNotShortenVisibleText) {
      latest.set(partId, {
        projection: { ...projection, partId, contentIndex: existing?.firstContentIndex ?? contentIndex },
        firstContentIndex: existing?.firstContentIndex ?? contentIndex,
      });
    }
  });
  const seenContent = new Set<string>();
  return [...latest.values()]
    .sort((left, right) => left.firstContentIndex - right.firstContentIndex)
    .map(({ projection }) => projection)
    .filter((projection) => {
      // A structured contract can be emitted once by the streaming callback
      // and once by the accepted final value.  Rolling deployments may give
      // those two parts different ids, so part-id coalescing alone is not
      // enough.  Collapse only exact same-lane/same-phase text; distinct
      // attempts or later model wording remain visible in the timeline.
      const key = [
        projection.scope,
        projection.namespace,
        projection.agentId,
        projection.taskId,
        projection.phase,
        projection.kind,
        projection.text,
      ].join('|');
      if (seenContent.has(key)) return false;
      seenContent.add(key);
      return true;
    });
};

export const teamProjectionMatchesMember = (
  projection: Pick<TeamModelProjection, 'taskId' | 'agentId'>,
  member: Pick<TeamMemberModel, 'taskId' | 'agentId'>,
): boolean => {
  if (projection.taskId && member.taskId && projection.taskId === member.taskId) return true;
  if (!projection.agentId || !member.agentId) return false;
  return projection.agentId === member.agentId
    || projection.agentId.startsWith(`${member.agentId}:`)
    || member.agentId.startsWith(`${projection.agentId}:`);
};

const samePartIdentity = (left: TeamPartRecord, right: TeamPartRecord): boolean => {
  if (left === right) return true;
  const leftIds = partIdentityValues(left);
  const rightIds = partIdentityValues(right);
  return leftIds.length > 0 && rightIds.length > 0
    && leftIds.some((leftId) => rightIds.some((rightId) => identitiesMatch(leftId, rightId)));
};

/**
 * Rebuild one expert's local chronological stream without changing ownership.
 * The parent message remains the transport for native parts, but this helper
 * makes the child projection explicit: model text, tools, and charts are
 * rendered only inside that expert's expandable subflow.
 */
export const teamMemberFlowFromParts = (
  parts: readonly unknown[],
  projections: readonly TeamModelProjection[],
  member: Pick<TeamMemberModel, 'taskId' | 'agentId' | 'tools' | 'charts'>,
): TeamMemberFlowItem[] => {
  const flow: TeamMemberFlowItem[] = [];
  const usedProjectionIds = new Set<string>();
  const usedToolIds = new Set<string>();
  const usedChartIds = new Set<string>();

  parts.forEach((value, contentIndex) => {
    if (!isRecord(value)) return;
    const part = value as TeamPartRecord;
    const projection = projections.find((candidate) => (
      candidate.contentIndex === contentIndex
      && candidate.scope === 'expert'
      && teamProjectionMatchesMember(candidate, member)
    ));
    if (projection && !usedProjectionIds.has(projection.partId)) {
      usedProjectionIds.add(projection.partId);
      flow.push({
        key: projection.partId,
        contentIndex,
        kind: 'projection',
        projection,
      });
    }

    if (part.type === 'tool-call') {
      const record = member.tools.find((candidate) => samePartIdentity(candidate.part, part));
      if (record) {
        const id = partToolId(record.part) || `${member.taskId}:tool:${contentIndex}`;
        if (!usedToolIds.has(id)) {
          usedToolIds.add(id);
          flow.push({
            key: id,
            contentIndex,
            kind: 'tool',
            tool: record,
          });
        }
      }
    }

    if (part.type === 'data' && normalizeText(part.name, 96) === 'stock-chart') {
      const record = member.charts.find((candidate) => samePartIdentity(candidate.part, part));
      if (record) {
        const id = normalizeText(recordValue(record.part, 'part_id', 'partId', 'id'), 192)
          || `${member.taskId}:chart:${contentIndex}`;
        if (!usedChartIds.has(id)) {
          usedChartIds.add(id);
          flow.push({
            key: id,
            contentIndex,
            kind: 'chart',
            chart: record,
          });
        }
      }
    }
  });

  return flow.sort((left, right) => left.contentIndex - right.contentIndex);
};

export const teamEventRole = (event: AgentStageEvent): TeamRole | undefined => eventRole(event);

export const teamEventTaskId = (event: AgentStageEvent): string => eventTaskId(event);

/**
 * Return the first user-facing progress sentence emitted by each worker.
 *
 * The raw stage history is intentional here. The reconciled presentation
 * history replaces a worker's `started` event with its terminal event, which
 * is correct for status badges but would lose the sentence that should anchor
 * that worker's card in the natural-language process stream.
 */
export const teamWorkerProgressAnchors = (
  stageData: readonly unknown[] | undefined,
): TeamWorkerProgressAnchor[] => {
  const seen = new Set<string>();
  return agentStageEvents(stageData)
    .filter((event) => (
      event.stage === 'planning'
      && event.status === 'started'
      && (event.actionId || '').endsWith(':worker')
    ))
    .map((event) => ({
      role: eventRole(event) || 'unknown',
      taskId: eventTaskId(event),
      agentId: eventAgentId(event),
      text: teamProgressText(event),
    }))
    .filter((anchor) => {
      if (!isWorkerRole(anchor.role) || !anchor.text) return false;
      const key = [anchor.role, anchor.taskId, anchor.agentId, anchor.text].join('|');
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
};

export const teamMemberForProgressAnchor = (
  members: readonly TeamMemberModel[],
  anchor: TeamWorkerProgressAnchor,
): TeamMemberModel | undefined => members.find((member) => (
  (anchor.taskId && member.taskId === anchor.taskId)
  || (anchor.agentId && member.agentId === anchor.agentId)
  || (anchor.role !== 'unknown' && member.role === anchor.role)
));

export const teamToolEventForPart = (
  part: TeamPartRecord,
  events: readonly AgentStageEvent[],
): AgentStageEvent | undefined => findToolEvent(part, events);

export const teamToolPart = (value: unknown): TeamPartRecord | undefined => {
  if (!isRecord(value) || value.type !== 'tool-call') return undefined;
  return value as TeamPartRecord;
};

export const buildTeamBoardModel = (
  rawParts: readonly unknown[],
  stageData: readonly unknown[] | undefined,
  trace: AgentExecutionTrace | null | undefined,
  active: boolean,
): TeamBoardModel => {
  const parts = rawParts.map((value) => (
    isRecord(value) ? value as TeamPartRecord : undefined
  )).filter((value): value is TeamPartRecord => Boolean(value));
  const events = reconcileTerminalStageEvents(orderedStageEvents([
    ...(stageData || []),
    ...stageEventsFromParts(parts),
  ]));
  const terminalRunStatus = [...events]
    .reverse()
    .find((event) => (
      (event.stage === 'publish' || event.stage === 'completed')
      && event.status !== 'started'
    ));
  const durableTeamStatus = statusValue(recordValue(teamTrace(trace), 'status'));
  const terminalOutcomeStatus = stageOutcomeStatus(terminalRunStatus);
  const team = teamTrace(trace);
  const reviewReports = new Map<string, TeamReviewReport>();
  parts.forEach((part) => {
    if (part.type !== 'data' || part.name !== 'team-review-report' || !isRecord(part.data)) return;
    if (part.data.scope !== 'review' || recordValue(part.data, 'report_source', 'reportSource') !== 'handoff') return;
    const partId = normalizeText(recordValue(part, 'part_id', 'partId'), 192);
    if (!partId || !Array.isArray(part.data.blocks)) return;
    const blocks = part.data.blocks.filter(isRecord).map((block) => ({
      section: normalizeText(block.section, 160),
      content: normalizeText(block.content, 12_000),
    })).filter((block) => Boolean(block.content));
    if (blocks.length > 0) reviewReports.set(partId, {
      partId,
      title: normalizeText(part.data.title, 240),
      blocks,
    });
  });
  const teamEvents = events.filter((event) => Boolean(eventRole(event)) && Boolean(
    recordValue(event.details, 'team_id', 'teamId'),
  ));
  const runtimeErrors = teamRuntimeErrors(events, trace?.toolResults);
  const isTeam = Boolean(team || teamEvents.length > 0);
  const tasks = taskRecords(team);
  const results = resultRecords(team);
  type MemberSeed = {
    role: TeamRole;
    label: string;
    taskId: string;
    agentId: string;
    task?: TraceRecord;
    result?: TraceRecord;
  };
  const memberMap = new Map<string, MemberSeed>();

  const ensureMember = (role: TeamRole, taskId = '', agentId = '', label = '') => {
    const key = taskId || agentId || role;
    const current = memberMap.get(key);
    if (current) {
      if (!current.taskId && taskId) current.taskId = taskId;
      if (!current.agentId && agentId) current.agentId = agentId;
      if (current.role === 'unknown' && role !== 'unknown') current.role = role;
      if (label && (!current.label || current.label === roleLabel(current.role))) current.label = label;
      return current;
    }
    const created: MemberSeed = { role, label: label || roleLabel(role), taskId, agentId };
    memberMap.set(key, created);
    return created;
  };

  tasks.forEach((task) => {
    const role = normalizedRole(recordValue(task, 'agent_id', 'agentId')) || 'unknown';
    const taskId = normalizeText(recordValue(task, 'task_id', 'taskId'), 96);
    const agentId = normalizeText(recordValue(task, 'agent_id', 'agentId'), 96);
    const label = normalizeText(recordValue(task, 'agent_display_name', 'agentDisplayName', 'display_name', 'displayName'), 160);
    const current = ensureMember(role, taskId, agentId, label);
    current.task = task;
  });

  results.forEach((result) => {
    const role = normalizedRole(recordValue(result, 'agent_id', 'agentId')) || 'unknown';
    const taskId = normalizeText(recordValue(result, 'task_id', 'taskId'), 96);
    const agentId = normalizeText(recordValue(result, 'agent_id', 'agentId'), 96);
    const label = normalizeText(recordValue(result, 'agent_display_name', 'agentDisplayName', 'display_name', 'displayName'), 160);
    const current = ensureMember(role, taskId, agentId, label);
    current.result = result;
    if (!current.taskId) current.taskId = taskId;
    if (!current.agentId) current.agentId = agentId;
  });

  // Reviewer lifecycle events belong to the review panel, not to a fourth
  // member workspace.  Keeping this boundary explicit prevents each review
  // action/agent identity from inflating the member count or rendering a
  // duplicate "综合审查" card.
  teamEvents.filter((event) => isWorkerRole(eventRole(event))).forEach((event) => {
    const role = eventRole(event) || 'unknown';
    const taskId = eventTaskId(event);
    const agentId = eventAgentId(event);
    const label = normalizeText(recordValue(event.details, 'agent_display_name', 'agentDisplayName', 'display_name', 'displayName'), 160);
    ensureMember(role, taskId, agentId, label);
  });

  const taskToolRoles = new Map<string, Set<TeamRole>>();
  tasks.forEach((task) => {
      const role = normalizedRole(recordValue(task, 'agent_id', 'agentId')) || 'unknown';
    const tools = recordValue(task, 'allowed_tools', 'allowedTools');
    if (!Array.isArray(tools)) return;
    tools.forEach((tool) => {
      const name = normalizeText(tool, 160);
      if (!name) return;
      const roles = taskToolRoles.get(name) || new Set<TeamRole>();
      roles.add(role);
      taskToolRoles.set(name, roles);
    });
  });

  const memberKeyForEvent = (event: AgentStageEvent | undefined): string | undefined => {
    if (!event) return undefined;
    const taskId = eventTaskId(event);
    const agentId = eventAgentId(event);
    const role = eventRole(event);
    if (taskId && memberMap.has(taskId)) return taskId;
    if (agentId && memberMap.has(agentId)) return agentId;
    if (role) {
      return [...memberMap.entries()].find(([, member]) => member.role === role)?.[0];
    }
    return undefined;
  };

  const modelProjections = teamModelProjectionsFromParts(parts);
  const toolsByMember = new Map<string, TeamToolRecord[]>();
  const chartsByMember = new Map<string, TeamChartRecord[]>();
  const unassignedTools: TeamToolRecord[] = [];
  const unassignedCharts: TeamChartRecord[] = [];

  const uniqueRoleForTool = (part: TeamPartRecord): TeamRole | undefined => {
    const roles = taskToolRoles.get(partToolName(part));
    if (!roles || roles.size !== 1) return undefined;
    return [...roles][0];
  };

  parts.forEach((part) => {
    if (part.type === 'tool-call') {
      const event = findToolEvent(part, events);
      const role = (event ? eventRole(event) : undefined) || uniqueRoleForTool(part);
      const key = memberKeyForEvent(event) || (
        role ? [...memberMap.entries()].find(([, member]) => member.role === role)?.[0] : undefined
      );
      const record = { part, event, runtimeError: findRuntimeErrorEvent(part, runtimeErrors) };
      if (!key) unassignedTools.push(record);
      else toolsByMember.set(key, [...(toolsByMember.get(key) || []), record]);
      return;
    }
    if (part.type !== 'data' || normalizeText(part.name, 96) !== 'stock-chart') return;
    const event = findChartEvent(part, events);
    const key = memberKeyForEvent(event);
    const record = { part, event };
    if (!key) unassignedCharts.push(record);
    else chartsByMember.set(key, [...(chartsByMember.get(key) || []), record]);
  });

  const members: TeamMemberModel[] = [...memberMap.entries()]
    .map(([key, seed]) => {
      const memberEvents = teamEvents.filter((event) => {
        const eventTask = eventTaskId(event);
        const eventAgent = eventAgentId(event);
        return (seed.taskId && eventTask === seed.taskId)
          || (seed.agentId && eventAgent === seed.agentId)
          || (!eventTask && !eventAgent && eventRole(event) === seed.role);
      });
      const result = seed.result;
      const projectedProgress = [...modelProjections].reverse().find((projection) => (
        (seed.taskId && projection.taskId === seed.taskId)
        || (seed.agentId && projection.agentId === seed.agentId)
        || (!projection.taskId && !projection.agentId && seed.role === 'unknown')
      ))?.text || '';
      const progress = memberProgress(memberEvents, projectedProgress);
      return {
        key,
        role: seed.role,
        label: seed.label || roleLabel(seed.role),
        taskId: seed.taskId,
        agentId: seed.agentId,
        status: terminalMemberStatus(memberStatus(result, memberEvents), statusValue(terminalRunStatus?.status)),
        progress,
        // Lifecycle/progress text is a status detail only. It must never be
        // promoted into the expanded card body as a replacement for model
        // output; structured worker summaries remain the detail source.
        summary: memberSummary(result),
        tools: toolsByMember.get(key) || [],
        charts: chartsByMember.get(key) || [],
        runtimeErrors: runtimeErrors.filter((event) => {
          // Tool-owned errors already appear at their call. Keep only
          // task-level errors here instead of showing both copies.
          if ((toolsByMember.get(key) || []).some((tool) => tool.runtimeError === event
            || (runtimeErrorId(event) && runtimeErrorId(tool.runtimeError) === runtimeErrorId(event)))) return false;
          const eventTask = eventTaskId(event);
          const eventAgent = eventAgentId(event);
          return (seed.taskId && eventTask === seed.taskId)
            || (seed.agentId && eventAgent === seed.agentId)
            || (!eventTask && !eventAgent && eventRole(event) === seed.role);
        }),
        result,
      };
    })
    .sort((left, right) => {
      const leftIndex = ROLE_ORDER.indexOf(left.role as (typeof ROLE_ORDER)[number]);
      const rightIndex = ROLE_ORDER.indexOf(right.role as (typeof ROLE_ORDER)[number]);
      return (leftIndex < 0 ? ROLE_ORDER.length : leftIndex)
        - (rightIndex < 0 ? ROLE_ORDER.length : rightIndex);
    });

  const coordinatorProgress = uniqueProgress(modelProjections
    .filter((projection) => projection.scope === 'coordinator')
    .map((projection) => projection.text));

  const review = [
    reviewPhase(team, 'evidence-merge', '证据合并', 'evidence_merge_status', 'evidence_merge', events),
    reviewPhase(team, 'review-dispatch', '复核调度', 'team_review_dispatch_status', 'review_dispatch', events),
    reviewPhase(team, 'conflict', '冲突检查', 'conflict_status', 'conflict', events),
    reviewPhase(team, 'critic', '独立复核', 'critic_status', 'critic', events),
    reviewPhase(team, 'review-gate', '复核汇合', 'team_review_gate_status', 'review_gate', events),
    reviewPhase(team, 'bull-case', '看多审查', 'bull_case_status', 'bull_case', events),
    reviewPhase(team, 'bear-case', '看空审查', 'bear_case_status', 'bear_case', events),
    reviewPhase(team, 'consensus', '共识判断', 'consensus_status', 'consensus', events),
    reviewPhase(team, 'criteria', '目标检查', 'criteria_status', 'criteria_assessment', events),
  ].filter((value): value is TeamReviewPhase => Boolean(value));

  const rawFailure = isRecord(team?.failure) ? team.failure : {};
  const terminalRunStatusRecord = isRecord(terminalRunStatus) ? terminalRunStatus : undefined;
  const planSource = normalizeText(recordValue(team, 'plan_source', 'planSource'), 32).toLowerCase();
  const planWasRejected = planSource === 'rejected';
  const dispatchedTaskIds = recordValue(team, 'dispatched_task_ids', 'dispatchedTaskIds');
  const hasDispatchedTasks = (Array.isArray(dispatchedTaskIds) && dispatchedTaskIds.length > 0)
    || Number(recordValue(team, 'dispatch_round', 'dispatchRound') || 0) > 0;
  const rawFailureStatus = statusValue(recordValue(rawFailure, 'status'));
  const terminalFailureStatus = statusValue(terminalRunStatus?.status);
  const failureStatus = rawFailureStatus && terminalStatuses.has(rawFailureStatus)
    ? rawFailureStatus
    : planWasRejected
      ? 'blocked'
      : durableTeamStatus === 'partial' || terminalOutcomeStatus === 'partial'
        ? undefined
        : durableTeamStatus && ['failed', 'blocked', 'cancelled'].includes(durableTeamStatus)
          ? durableTeamStatus
          : terminalFailureStatus && terminalStatuses.has(terminalFailureStatus)
        ? terminalFailureStatus
        : statusValue(recordValue(team, 'status'));
  const isTerminalFailure = failureStatus === 'failed'
    || failureStatus === 'blocked'
    || failureStatus === 'cancelled';
  const failure = isTerminalFailure
    ? {
      status: failureStatus,
      errorCode: normalizeText(
        recordValue(rawFailure, 'error_code', 'errorCode')
          || (planWasRejected ? 'team_plan_rejected' : '')
          || recordValue(terminalRunStatusRecord, 'error_code', 'errorCode'),
        128,
      ),
      detail: normalizeText(
        recordValue(rawFailure, 'detail', 'reason')
          || (planWasRejected ? recordValue(team, 'plan_error', 'planError') : '')
          || recordValue(terminalRunStatusRecord, 'summary'),
        1_200,
      ) || 'Team 协作未能进入专家执行阶段。',
      phase: normalizeText(
        recordValue(rawFailure, 'phase')
          || recordValue(isRecord(team?.collaboration) ? team.collaboration : undefined, 'phase'),
        64,
      ),
      dispatchStatus: normalizeText(
        recordValue(rawFailure, 'dispatch_status', 'dispatchStatus'),
        32,
      ) || (hasDispatchedTasks ? 'stopped' : 'not_started'),
    }
    : null;

  const overallStatus = planWasRejected
    ? 'blocked'
    : durableTeamStatus === 'partial' || terminalOutcomeStatus === 'partial'
      ? 'partial'
      : durableTeamStatus && terminalStatuses.has(durableTeamStatus)
        ? durableTeamStatus
        : statusValue(terminalRunStatus?.status)
    || (active ? 'running' : members.some((member) => member.status === 'partial') ? 'partial' : 'completed');
  const problem = overallStatus === 'partial'
    || overallStatus === 'failed'
    || overallStatus === 'blocked'
    || members.some((member) => ['partial', 'failed', 'blocked', 'cancelled'].includes(member.status));

  return {
    isTeam,
    teamId: normalizeText(recordValue(team, 'team_id', 'teamId'), 96),
    status: overallStatus,
    members,
    review,
    reviewReports: [...reviewReports.values()],
    coordinatorProgress,
    unassignedTools,
    unassignedCharts,
    totalToolCount: members.reduce((count, member) => count + member.tools.length, 0) + unassignedTools.length,
    runtimeErrors,
    problem: problem || Boolean(failure),
    failure,
  };
};
