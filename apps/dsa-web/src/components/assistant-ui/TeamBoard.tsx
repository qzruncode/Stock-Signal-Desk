import type { FC } from 'react';
import { useCallback, useId, useMemo, useState } from 'react';
import { useMessage } from '@assistant-ui/react';
import { ChevronRightIcon, CircleAlertIcon, Loader2Icon, UsersRoundIcon } from 'lucide-react';
import { AssistantMarkdown } from './AssistantMarkdownText';
import { AssistantTypingIndicator } from './AssistantTypingIndicator';
import { cn } from '../../utils/cn';
import { useTeamBoardState } from './TeamBoardState';
import { teamMemberFlowFromParts, teamModelProjectionsFromParts, type TeamBoardModel, type TeamFailureModel, type TeamMemberFlowItem, type TeamModelProjection, type TeamMemberModel } from './TeamBoardUtils';
import { terminalStatuses, statusLabel, statusProblem } from './TeamBoardViewUtils';
import { TeamMemberCard, TeamReviewCard } from './TeamBoardLanes';
export { TeamMemberCard, TeamReviewCard } from './TeamBoardLanes';
const TeamLaneProjection: FC<{
  projection: TeamModelProjection;
  animate: boolean;
  onAnimationComplete?: () => void;
}> = ({ projection, animate, onAnimationComplete }) => (
    <div
      className="min-w-0"
      data-team-lane-projection
      data-team-main-projection={projection.scope}
      data-team-projection-id={projection.partId}
      data-team-model-projection={projection.scope}
      data-team-model-projection-kind={projection.kind}
    >
      <AssistantMarkdown
        text={projection.text}
        animate={animate}
        onAnimationComplete={onAnimationComplete}
      />
    </div>
  );

const teamFailureTitle = (failure: TeamFailureModel): string => {
  if (failure.status === 'blocked') return 'Team 协作已阻塞';
  if (failure.status === 'cancelled') return 'Team 协作已取消';
  return 'Team 协作失败';
};

const teamFailureReason = (failure: TeamFailureModel): string => {
  if (failure.errorCode === 'team_plan_rejected') {
    return '协作计划未通过服务端边界校验，本轮没有分发专家任务。';
  }
  if (failure.errorCode === 'team_not_applicable') {
    return '当前问题无法形成有效的多专家分工，本轮没有分发专家任务。';
  }
  if (failure.errorCode === 'team_plan_failed') {
    return '协作计划生成或校验失败，本轮没有执行专家任务。';
  }
  return failure.detail || '本轮协作没有形成可用的专家结果。';
};

const TeamFailureReceipt: FC<{
  failure: TeamFailureModel;
}> = ({ failure }) => {
  const reason = teamFailureReason(failure);
  const hasDiagnostic = Boolean(failure.detail && failure.detail !== reason);
  return (
    <section
      className="rounded-xl border border-amber-200/80 bg-amber-50/60 px-3 py-2.5 text-sm"
      role="status"
      aria-live="polite"
      aria-label={teamFailureTitle(failure)}
      data-team-failure-receipt
    >
      <div className="flex min-w-0 items-start gap-2">
        <CircleAlertIcon className="mt-0.5 size-4 shrink-0 text-amber-700" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="font-medium text-foreground">{teamFailureTitle(failure)}</span>
            <span className="text-xs text-amber-800/80">
              {failure.dispatchStatus === 'not_started' ? '未分发专家任务' : '已停止后续任务'}
            </span>
          </div>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">{reason}</p>
          {hasDiagnostic ? (
            <p className="mt-1 break-words text-[11px] leading-5 text-muted-foreground/80" data-team-failure-detail>
              原因：{failure.detail}
            </p>
          ) : null}
        </div>
      </div>
    </section>
  );
};

const TeamChildExecutionGroup: FC<{
  members: TeamMemberModel[];
  flows: Map<string, TeamMemberFlowItem[]>;
  active: boolean;
}> = ({ members, flows, active }) => {
  if (members.length === 0) return null;
  const runningCount = members.filter((member) => (
    member.status === 'running' || member.status === 'waiting'
  )).length;
  return (
    <section
      className="min-w-0"
      aria-label="子 Agent 执行集合"
      data-team-child-group
    >
      <header className="flex min-w-0 items-center gap-2 border-b border-border/60 py-1 text-xs text-muted-foreground">
        <UsersRoundIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="shrink-0 font-medium text-foreground">子 Agent</span>
        <span className="min-w-0 truncate">
          {runningCount > 0 ? `${runningCount} 个执行中` : `${members.length} 个方向已返回`}
        </span>
      </header>
      <div className="divide-y divide-border/60" data-team-child-members>
        {members.map((member) => (
          <div key={member.key} className="first:pt-0 last:pb-0" data-team-child-member>
            <TeamMemberCard
              member={member}
              flow={flows.get(member.key)}
            />
          </div>
        ))}
      </div>
      {active && runningCount === 0 ? (
        <div className="sr-only" data-team-child-group-active-state>
          子 Agent 当前没有正在执行的任务
        </div>
      ) : null}
    </section>
  );
};

type TeamTimelineEntry =
  | { kind: 'projection'; key: string; position: number; projection: TeamModelProjection }
  | { kind: 'children'; key: string; position: number }
  | { kind: 'review'; key: string; position: number };

/**
 * Project one Team message into a main narrative spine with one compact child
 * execution group.  Child text/tools/charts stay in the child member's local
 * flow; only coordinator/review model projections remain on the main spine.
 *
 * This is deliberately a projection, not a second runtime.  The root content
 * keeps the original order for replay, while the view changes only the visual
 * parent of expert parts so parallel work cannot appear as three top-level
 * assistant narratives.
 */
export const TeamCollaborationView: FC = () => {
  const { active, model } = useTeamBoardState();
  const content = useMessage((state) => state.content);
  const [completedCoordinatorProjections, setCompletedCoordinatorProjections] = useState<Set<string>>(
    () => new Set(),
  );
  const nativeProjections = useMemo(
    () => teamModelProjectionsFromParts(content),
    [content],
  );
  const projections = nativeProjections;
  const coordinatorProjections = projections.filter((projection) => projection.scope === 'coordinator');
  const reviewProjections = projections.filter((projection) => projection.scope === 'review');
  const reviewIsRunning = model.review.some((phase) => (
    phase.status === 'running' || phase.status === 'waiting'
  ));
  const firstCoordinatorProjection = coordinatorProjections[0];
  const coordinatorNarrativeReady = !active
    || !firstCoordinatorProjection
    || completedCoordinatorProjections.has(firstCoordinatorProjection.partId);
  const memberFlows = useMemo(() => (
    new Map(
      model.members.map((member) => [
        member.key,
        teamMemberFlowFromParts(content, projections, member),
      ] as const),
    )
  ), [content, model.members, projections]);

  const timeline = useMemo<TeamTimelineEntry[]>(() => {
    const coordinatorEntries = coordinatorProjections.map((projection) => ({
      kind: 'projection' as const,
      key: projection.partId,
      position: projection.contentIndex,
      projection,
    }));
    const childIndexes = [...memberFlows.values()]
      .flatMap((flow) => flow.map((item) => item.contentIndex))
      .filter((index) => Number.isFinite(index) && index < Number.MAX_SAFE_INTEGER - 1_000);
    const firstChildIndex = childIndexes.length > 0 ? Math.min(...childIndexes) : undefined;
    const firstCoordinatorKey = coordinatorProjections[0]?.partId;
    const beforeChild = coordinatorEntries.filter((entry) => (
      entry.key === firstCoordinatorKey
      || firstChildIndex == null
      || entry.projection.contentIndex <= firstChildIndex
    ));
    const afterChild = coordinatorEntries.filter((entry) => !beforeChild.includes(entry));
    const entries: TeamTimelineEntry[] = [];
    let position = 0;
    beforeChild.forEach((entry) => {
      entries.push({ ...entry, position });
      position += 1;
    });

    if (model.members.length > 0) {
      entries.push({
        kind: 'children',
        key: `${model.teamId || 'team'}:child-execution-group`,
        position,
      });
      position += 1;
    }

    afterChild.forEach((entry) => {
      entries.push({ ...entry, position });
      position += 1;
    });

    if (reviewProjections.length > 0 || model.review.length > 0 || model.reviewReports?.length) {
      entries.push({
        kind: 'review',
        key: `${model.teamId || 'team'}:review-card`,
        // Review is a semantic phase after the coordinator's handoff
        // narrative.  It must not use a provisional child/content index: the
        // review projection can arrive after the card's lifecycle event and
        // otherwise make the whole lane jump when that projection is appended.
        position,
      });
    }
    return entries.sort((left, right) => left.position - right.position);
  }, [coordinatorProjections, memberFlows, model.members.length, model.review.length, model.reviewReports?.length, model.teamId, reviewProjections]);

  const markCoordinatorProjectionComplete = useCallback((partId: string) => {
    setCompletedCoordinatorProjections((current) => {
      if (current.has(partId)) return current;
      const next = new Set(current);
      next.add(partId);
      return next;
    });
  }, []);

  return (
    <div className="min-w-0" data-team-collaboration-view>
      {model.failure ? <TeamFailureReceipt failure={model.failure} /> : null}
      <div
        className="min-w-0 space-y-2"
        role="region"
        data-team-main-timeline
        aria-label="Team主 Agent 时间线"
      >
        {timeline.map((entry) => {
          if (entry.kind === 'projection') {
            return (
              <TeamLaneProjection
                key={entry.key}
                projection={entry.projection}
                animate={active}
                onAnimationComplete={
                  entry.projection.scope === 'coordinator'
                    ? () => markCoordinatorProjectionComplete(entry.projection.partId)
                    : undefined
                }
              />
            );
          }
          if (entry.kind === 'children') {
            if (!coordinatorNarrativeReady) return null;
            return (
              <div key={entry.key} className="min-w-0" data-team-main-child-anchor>
                <TeamChildExecutionGroup
                  members={model.members}
                  flows={memberFlows}
                  active={active}
                />
              </div>
            );
          }
          if (!coordinatorNarrativeReady) return null;
          return (
            <div key={entry.key} className="min-w-0" data-team-main-review-anchor>
              <TeamReviewCard model={model} active={active} projections={reviewProjections} />
            </div>
          );
        })}
        {active && !model.failure && !reviewIsRunning ? (
          <AssistantTypingIndicator />
        ) : null}
      </div>
    </div>
  );
};

const teamSummary = (model: TeamBoardModel, active: boolean): string => {
  const completed = model.members.filter((member) => member.status === 'completed').length;
  const problem = model.members.filter((member) => statusProblem(member.status)).length;
  const pending = model.members.filter((member) => (
    member.status === 'running' || member.status === 'queued' || member.status === 'waiting'
  )).length;
  const activePhase = model.review.find((phase) => phase.status === 'running' || phase.status === 'waiting');
  const nextPhase = model.review.find((phase) => !terminalStatuses.has(phase.status));
  const latestCoordinatorProgress = model.coordinatorProgress.at(-1);
  if (model.failure) {
    return model.failure.dispatchStatus === 'not_started'
      ? '未分发专家任务'
      : '后续协作已停止';
  }
  if (activePhase) return `正在${activePhase.label}`;
  if (active && pending) return `${completed}/${model.members.length || 0} 个方向已返回，${pending} 个方向处理中`;
  if (active && nextPhase) return `成员核验完成，准备${nextPhase.label}`;
  if (active && latestCoordinatorProgress) return latestCoordinatorProgress;
  if (active) return '已收到当前阶段结果，等待下一条协作事件';
  if (problem) return `${completed}/${model.members.length || 0} 个方向完成 · ${problem} 个方向存在缺口`;
  return `${completed || model.members.length}/${model.members.length || 0} 个方向已完成`;
};

export const TeamBoard: FC<{
  selectedTeam?: boolean;
  /** Render only the compact Team status row; member cards belong to the process anchors. */
  layout?: 'full' | 'summary';
}> = ({ selectedTeam = false, layout = 'full' }) => {
  const { active, model } = useTeamBoardState();
  const messageId = useMessage((state) => state.id);
  const [expandedOverride, setExpandedOverride] = useState<{ messageId: string; expanded: boolean } | null>(null);
  const detailId = useId();

  if (!model.isTeam) {
    if (!active || !selectedTeam) return null;
    return (
      <section className="mb-4 min-w-0 rounded-xl border border-primary/20 bg-primary/[0.035] p-3" aria-label="Team协作分析" data-team-board>
        <div className="flex min-w-0 items-center gap-2" role="status" aria-live="polite">
          <Loader2Icon className="size-4 shrink-0 animate-spin text-primary" aria-hidden="true" />
          <span className="min-w-0 truncate text-sm font-medium text-foreground">Team 协作分析 · 准备中</span>
          <span className="min-w-0 truncate text-xs text-muted-foreground">正在准备并行核验方向</span>
        </div>
      </section>
    );
  }
  const expanded = active || (
    expandedOverride?.messageId === messageId && expandedOverride.expanded
  );
  const statusText = active
    ? '执行中'
    : model.failure
      ? statusLabel(model.failure.status)
      : model.problem
        ? '部分完成'
        : '已完成';
  const triggerText = `Team 协作分析 · ${statusText} · ${teamSummary(model, active)}`;

  if (layout === 'summary') {
    return (
      <section className="mb-3 min-w-0" aria-label="Team协作分析" data-team-board data-team-board-layout="summary">
        <div
          className={cn(
            'flex min-w-0 items-center gap-2 border-b py-2 text-sm',
            active ? 'border-primary/15 text-foreground' : 'border-border/70 text-muted-foreground',
          )}
          role={active ? 'status' : undefined}
          aria-live={active ? 'polite' : undefined}
        >
          <UsersRoundIcon className={cn('size-4 shrink-0', model.problem ? 'text-amber-600' : 'text-primary')} aria-hidden="true" />
          <span className="min-w-0 truncate font-medium">Team 协作分析 · {statusText}</span>
          <span className="min-w-0 truncate text-xs text-muted-foreground">{teamSummary(model, active)}</span>
        </div>
      </section>
    );
  }

  return (
    <section className="mb-4 min-w-0" aria-label="Team协作分析" data-team-board>
      {active ? (
        <div className="flex min-w-0 items-center gap-2 border-b border-primary/15 pb-2 text-sm text-foreground" role="status" aria-live="polite">
          <Loader2Icon className="size-4 shrink-0 animate-spin text-primary" aria-hidden="true" />
          <span className="min-w-0 truncate font-medium">Team 协作分析 · 执行中</span>
          <span className="min-w-0 truncate text-xs text-muted-foreground">{teamSummary(model, active)}</span>
        </div>
      ) : (
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={detailId}
          aria-label={`${expanded ? '收起' : '展开'}${triggerText}`}
          onClick={() => setExpandedOverride((value) => ({
            messageId,
            expanded: value?.messageId === messageId ? !value.expanded : true,
          }))}
          className="flex w-full min-w-0 items-center gap-2 border-b border-border/70 py-2 text-left text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
        >
          <UsersRoundIcon className={cn('size-4 shrink-0', model.problem ? 'text-amber-600' : 'text-primary')} aria-hidden="true" />
          <span className="min-w-0 flex-1 truncate">{triggerText}</span>
          <ChevronRightIcon className={cn('size-4 shrink-0 transition-transform', expanded && 'rotate-90')} aria-hidden="true" />
        </button>
      )}

      <div
        id={detailId}
        role="region"
        aria-label="Team成员工作区"
        aria-hidden={!expanded}
        className="grid transition-[grid-template-rows] duration-300 ease-out"
        style={{ gridTemplateRows: expanded ? '1fr' : '0fr' }}
      >
        <div className="min-h-0 overflow-hidden">
          <div className="pt-3">
            {model.failure ? <TeamFailureReceipt failure={model.failure} /> : null}
            {model.members.length > 0 ? (
              <div className="grid min-w-0 gap-3 lg:grid-cols-3">
                {model.members.map((member) => (
                  <TeamMemberCard key={member.key} member={member} />
                ))}
              </div>
            ) : null}
            <TeamReviewCard model={model} active={active} />
          </div>
        </div>
      </div>
    </section>
  );
};
