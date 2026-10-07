import type { FC } from 'react';
import { useCallback, useId, useState } from 'react';
import { ActivityIcon, BarChart3Icon, CheckCircle2Icon, ChevronRightIcon, CircleAlertIcon, Clock3Icon, ClipboardCheckIcon, Loader2Icon, WrenchIcon } from 'lucide-react';
import { AssistantMarkdown } from './AssistantMarkdownText';
import { AgentToolCallPart } from './AgentReasoning';
import { ChartReference } from './StructuredAnswerReferences';
import { normalizeChartReference } from './StructuredAnswerReferencesUtils';
import { cn } from '../../utils/cn';
import { isRecord } from './AgentReasoningUtils';
import { recoveryStatusLabel } from './TeamRuntimeErrors';
import { Tooltip } from '../common/Tooltip';
import { teamRoleLabel, type TeamBoardModel, type TeamMemberFlowItem, type TeamModelProjection, type TeamMemberModel, type TeamMemberStatus, type TeamToolRecord } from './TeamBoardUtils';
import { terminalStatuses, statusLabel, statusProblem, statusClassName, roleIconClassName, toolPartProps } from './TeamBoardViewUtils';
const StatusIcon: FC<{ status: TeamMemberStatus; className?: string }> = ({ status, className }) => {
  if (statusProblem(status)) {
    return <CircleAlertIcon className={className} aria-hidden="true" />;
  }
  if (status === 'completed') {
    return <CheckCircle2Icon className={className} aria-hidden="true" />;
  }
  if (status === 'queued' || status === 'waiting') {
    return <Clock3Icon className={className} aria-hidden="true" />;
  }
  return <Loader2Icon className={cn(className, 'animate-spin')} aria-hidden="true" />;
};

const runtimeReceipt = (event: TeamToolRecord['runtimeError']): Record<string, unknown> => (
  event && isRecord(event.details?.runtime_error)
    ? event.details.runtime_error
    : {}
);

export const RuntimeErrorLine: FC<{ event?: TeamToolRecord['runtimeError'] }> = ({ event }) => {
  if (!event) return null;
  const receipt = runtimeReceipt(event);
  const code = String(receipt.error_code ?? receipt.errorCode ?? event.errorCode ?? 'runtime_error');
  const exceptionType = String(receipt.exception_type ?? receipt.exceptionType ?? '').trim();
  const message = String(receipt.message ?? event.summary ?? '').trim();
  const fallbackStatus = String(receipt.fallback_status ?? receipt.fallbackStatus ?? '').trim();
  const fallbackCallIds = receipt.fallback_call_ids ?? receipt.fallbackCallIds;
  const location = [receipt.node, receipt.phase].filter(Boolean).join(' / ');
  const traceback = String(receipt.sanitized_traceback ?? receipt.sanitizedTraceback ?? '');
  const retryable = receipt.retryable === true;
  return (
    <details className="mt-1 rounded-md border border-amber-200/70 bg-amber-50/50 px-2 py-1.5 text-[11px] text-amber-900" data-team-runtime-error>
      <summary className="flex cursor-pointer list-none items-center gap-1.5">
        <CircleAlertIcon className="size-3 shrink-0" aria-hidden="true" />
        <span className="truncate">运行异常 · {code}</span>
        {fallbackStatus ? <span className="shrink-0 text-amber-800/70">网页恢复：{recoveryStatusLabel(fallbackStatus)}</span> : null}
      </summary>
      <div className="mt-1 space-y-0.5 break-words pl-4 leading-4 text-amber-900/80">
        {exceptionType ? <div>类型：{exceptionType}</div> : null}
        {message ? <div>原因：{message}</div> : null}
        {location ? <div>位置：{location}</div> : null}
        {typeof receipt.attempt === 'number' ? <div>调用尝试：{receipt.attempt} 次</div> : null}
        {Array.isArray(fallbackCallIds) && fallbackCallIds.length > 0 ? (
          <div className="break-all">恢复调用：{fallbackCallIds.join('、')}</div>
        ) : null}
        {retryable ? <div>处理：已标记为可重试</div> : null}
        {traceback ? <details><summary className="cursor-pointer">脱敏异常堆栈</summary><pre className="whitespace-pre-wrap break-all">{traceback}</pre></details> : null}
      </div>
    </details>
  );
};

const TeamToolList: FC<{
  tools: TeamToolRecord[];
}> = ({ tools }) => {
  const [expanded, setExpanded] = useState(false);
  const detailId = useId();
  if (tools.length === 0) return null;
  const completed = tools.filter((tool) => (
    tool.part.result !== undefined || terminalStatuses.has(tool.event?.status as TeamMemberStatus)
  )).length;
  const failed = tools.filter((tool) => statusProblem(
    tool.event?.status as TeamMemberStatus || (tool.part.isError ? 'failed' : 'running'),
  )).length;
  const summary = [
    `${tools.length} 个工具`,
    completed ? `${completed} 个已完成` : '',
    failed ? `${failed} 个异常` : '',
  ].filter(Boolean).join(' · ');
  return (
    <div className="mt-3 border-t border-border/50 pt-2">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={detailId}
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center gap-2 text-left text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
      >
        <WrenchIcon className="size-3.5 shrink-0" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate">查看 {summary}</span>
        <ChevronRightIcon
          className={cn('size-3.5 shrink-0 transition-transform', expanded && 'rotate-90')}
          aria-hidden="true"
        />
      </button>
      <div
        id={detailId}
        role="region"
        aria-label="成员工具调用"
        aria-hidden={!expanded}
        className="grid transition-[grid-template-rows] duration-200 ease-out"
        style={{ gridTemplateRows: expanded ? '1fr' : '0fr' }}
      >
        <div className="min-h-0 overflow-hidden">
          <div className="mt-1 pl-1">
            {tools.map((tool, index) => (
              <div key={`${tool.part.toolCallId ?? tool.part.tool_call_id ?? index}`}>
                <AgentToolCallPart
                  {...toolPartProps(tool)}
                  compact
                />
                <RuntimeErrorLine event={tool.runtimeError} />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

const TeamMemberFlow: FC<{
  flow: TeamMemberFlowItem[];
  animate: boolean;
}> = ({ flow, animate }) => {
  const [completedProjectionText, setCompletedProjectionText] = useState<Map<string, string>>(() => new Map());
  const markProjectionComplete = useCallback((partId: string, text: string) => {
    setCompletedProjectionText((current) => {
      if (current.get(partId) === text) return current;
      const next = new Map(current);
      next.set(partId, text);
      return next;
    });
  }, []);

  return (
    <div className="mt-2 space-y-1" data-team-child-flow>
      {flow.map((item, itemIndex) => {
        if (item.kind === 'projection' && item.projection) {
          const projection = item.projection;
          return (
            <div
              key={item.key}
              className="min-w-0 motion-safe:animate-in motion-safe:fade-in motion-safe:duration-200"
              data-team-child-projection
              data-team-projection-id={projection.partId}
            >
              <AssistantMarkdown
                text={projection.text}
                animate={animate}
                onAnimationComplete={() => markProjectionComplete(projection.partId, projection.text)}
              />
            </div>
          );
        }
        const previousProjection = flow
          .slice(0, itemIndex)
          .reverse()
          .find((candidate) => candidate.kind === 'projection')?.projection;
        const projectionStillRevealing = Boolean(
          animate
          && previousProjection
          && completedProjectionText.get(previousProjection.partId) !== previousProjection.text,
        );
        if (projectionStillRevealing) return null;
        if (item.kind === 'tool' && item.tool) {
          return (
            <div key={item.key} className="min-w-0 pl-0.5" data-team-child-tool>
              <AgentToolCallPart {...toolPartProps(item.tool)} compact />
              <RuntimeErrorLine event={item.tool.runtimeError} />
            </div>
          );
        }
        if (item.kind === 'chart' && item.chart) {
          const reference = normalizeChartReference(item.chart.part.data);
          return reference ? (
            <div key={item.key} className="min-w-0" data-team-child-chart>
              <ChartReference reference={reference} />
            </div>
          ) : null;
        }
        return null;
      })}
    </div>
  );
};

export const TeamMemberCard: FC<{
  member: TeamMemberModel;
  flow?: TeamMemberFlowItem[];
}> = ({ member, flow }) => {
  const [expanded, setExpanded] = useState(false);
  const detailId = useId();
  // A pre-fix run may have persisted a structured worker report after the
  // worker had already failed.  That report can say "no tools ran" even when
  // the same card contains real tool records.  Failed workers must expose the
  // error and retained tools, not replay an invalid terminal report.  Live
  // progress projections remain visible in their original order.
  const visibleFlow = flow?.filter((item) => !(
    member.status === 'failed'
    && item.kind === 'projection'
    && item.projection?.kind === 'report'
  ));
  const hasVisibleFlow = Boolean(visibleFlow && visibleFlow.length > 0);
  const flowHasProjection = Boolean(visibleFlow?.some((item) => item.kind === 'projection'));
  const animateFlow = member.status === 'running' || member.status === 'waiting';
  const icon = member.role === 'market'
    ? <BarChart3Icon className="size-4" aria-hidden="true" />
    : member.role === 'fundamental'
      ? <ClipboardCheckIcon className="size-4" aria-hidden="true" />
      : <ActivityIcon className="size-4" aria-hidden="true" />;
  return (
    <section
      className="min-w-0 py-2"
      aria-label={`${member.label}工作区`}
      data-team-member={member.role}
    >
      <header className="min-w-0">
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={detailId}
          aria-label={`${expanded ? '收起' : '展开'}${member.label}工作区`}
          onClick={() => setExpanded((value) => !value)}
          className="flex w-full min-w-0 items-center gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
        >
          <div className={cn('flex size-5 shrink-0 items-center justify-center', roleIconClassName(member.role))}>
            {icon}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <h3 className="min-w-0 truncate text-sm font-medium text-foreground">{member.label || teamRoleLabel(member.role)}</h3>
              <span className={cn('inline-flex shrink-0 items-center gap-1 text-[11px]', statusClassName(member.status))}>
                {member.progress ? (
                  <Tooltip
                    content={member.progress}
                    ariaLabel={`${member.label || teamRoleLabel(member.role)}状态说明`}
                    contentClassName="max-w-[20rem]"
                  >
                    <StatusIcon status={member.status} className="size-3.5" />
                  </Tooltip>
                ) : (
                  <StatusIcon status={member.status} className="size-3.5" />
                )}
                {statusLabel(member.status)}
              </span>
            </div>
          </div>
          <ChevronRightIcon
            className={cn('size-3.5 shrink-0 text-muted-foreground transition-transform', expanded && 'rotate-90')}
            aria-hidden="true"
          />
        </button>
      </header>

      {expanded ? (
        <div id={detailId} className="min-w-0" role="region" aria-label={`${member.label}详情`}>
          {member.summary
            && member.summary !== member.progress
            && member.status !== 'failed'
            && !flowHasProjection ? (
            <div className="mt-2 pl-7 text-xs leading-5 text-foreground/85">
              <AssistantMarkdown text={member.summary} />
            </div>
          ) : null}

          {member.runtimeErrors.length > 0 ? (
            <div className="mt-2 space-y-1 pl-7" data-team-member-runtime-errors>
              {member.runtimeErrors.map((event, index) => (
                <RuntimeErrorLine key={`${member.key}-runtime-error-${index}`} event={event} />
              ))}
            </div>
          ) : null}

          {hasVisibleFlow ? (
            <TeamMemberFlow flow={visibleFlow || []} animate={animateFlow} />
          ) : (
            <>
              {member.charts.length > 0 ? (
                <div className="mt-2 space-y-2" data-team-member-charts>
                  {member.charts.map((chart, index) => {
                    const reference = normalizeChartReference(chart.part.data);
                    return reference ? <ChartReference key={`${member.key}-chart-${index}`} reference={reference} /> : null;
                  })}
                </div>
              ) : null}
              <TeamToolList tools={member.tools} />
            </>
          )}
        </div>
      ) : null}
    </section>
  );
};

const TeamReviewFlow: FC<{
  projections: TeamModelProjection[];
  animate: boolean;
}> = ({ projections, animate }) => {
  if (projections.length === 0) return null;
  return (
    <div className="mt-2 space-y-1 pl-7" data-team-review-flow>
      {projections.map((projection) => (
        <div
          key={projection.partId}
          className="min-w-0 motion-safe:animate-in motion-safe:fade-in motion-safe:duration-200"
          data-team-review-projection
          data-team-projection-id={projection.partId}
        >
          <AssistantMarkdown text={projection.text} animate={animate} />
        </div>
      ))}
    </div>
  );
};

export const TeamReviewCard: FC<{
  model: TeamBoardModel;
  active: boolean;
  projections?: TeamModelProjection[];
}> = ({ model, active, projections = [] }) => {
  const [expanded, setExpanded] = useState(false);
  const detailId = useId();
  const reviewProjections = projections.filter((projection) => projection.scope === 'review');
  if (
    model.review.length === 0
    && reviewProjections.length === 0
    && !model.reviewReports?.length
    && model.unassignedTools.length === 0
    && model.unassignedCharts.length === 0
  ) {
    return null;
  }
  const activePhase = model.review.find((phase) => phase.status === 'running' || phase.status === 'waiting');
  const reviewActive = Boolean(activePhase);
  const completedReviewCount = model.review.filter((phase) => phase.status === 'completed').length;
  const problemReviewCount = model.review.filter((phase) => statusProblem(phase.status)).length;
  const terminalSummary = problemReviewCount > 0
    ? `${problemReviewCount} 项复核存在缺口`
    : model.review.length > 0
      ? `${completedReviewCount}/${model.review.length} 项复核已完成`
      : '公共结果已完成整理';
  const reviewStatus: TeamMemberStatus = reviewActive
    ? activePhase?.status || 'running'
    : problemReviewCount > 0
      ? 'partial'
      : model.review.length > 0 && completedReviewCount === model.review.length
        ? 'completed'
        : 'waiting';
  return (
    <section
      className="min-w-0 py-2"
      aria-label="Team综合审查"
      data-team-review-lane
    >
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={detailId}
        aria-label={`${expanded ? '收起' : '展开'}综合审查`}
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full min-w-0 items-center gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
      >
        <div className="flex size-5 shrink-0 items-center justify-center text-primary">
          <ClipboardCheckIcon className="size-4" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <h3 className="min-w-0 truncate text-sm font-medium text-foreground">综合审查</h3>
            <span className={cn('inline-flex shrink-0 items-center gap-1 text-[11px]', statusClassName(reviewStatus))}>
              <StatusIcon status={reviewStatus} className="size-3.5" />
              {reviewActive && activePhase?.label ? `正在${activePhase.label}` : statusLabel(reviewStatus)}
            </span>
          </div>
        </div>
        <span className="min-w-0 truncate text-xs text-muted-foreground">{terminalSummary}</span>
        <ChevronRightIcon
          className={cn('size-3.5 shrink-0 text-muted-foreground transition-transform', expanded && 'rotate-90')}
          aria-hidden="true"
        />
      </button>
      {expanded ? (
        <div id={detailId} role="region" aria-label="综合审查详情">
          <TeamReviewFlow projections={reviewProjections} animate={active && reviewActive} />
          {model.reviewReports?.map((report) => (
            <div key={report.partId} className="mt-3 space-y-2 pl-7" data-team-review-report>
              {report.title ? <h4 className="text-sm font-medium">{report.title}</h4> : null}
              {report.blocks.map((block, index) => (
                <div key={`${report.partId}:${index}`} className="min-w-0">
                  {block.section ? <h5 className="mb-1 text-sm font-medium">{block.section}</h5> : null}
                  <AssistantMarkdown text={block.content} animate={false} />
                </div>
              ))}
            </div>
          ))}
          {model.review.length > 0 ? (
            <ul className="mt-2 divide-y divide-border/50 pl-7" data-team-review-phases>
              {model.review.map((phase) => (
                <li key={phase.key} className="flex min-w-0 items-start gap-2 py-2 text-xs">
                  <StatusIcon status={phase.status} className={cn('mt-0.5 size-3.5 shrink-0', statusClassName(phase.status))} />
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <span className="font-medium text-foreground">{phase.label}</span>
                      <span className="text-muted-foreground">{statusLabel(phase.status)}</span>
                    </div>
                    {phase.summary ? <div className="mt-0.5 text-muted-foreground">{phase.summary}</div> : null}
                  </div>
                </li>
              ))}
            </ul>
          ) : null}
          {model.unassignedTools.length > 0 ? (
            <div className="mt-3 pl-7">
              <div className="mb-1 text-xs font-medium text-muted-foreground">公共主体确认</div>
              <TeamToolList tools={model.unassignedTools} />
            </div>
          ) : null}
          {model.unassignedCharts.length > 0 ? (
            <div className="mt-3 space-y-2 pl-7" data-team-unassigned-charts>
              {model.unassignedCharts.map((chart, index) => {
                const reference = normalizeChartReference(chart.part.data);
                return reference ? <ChartReference key={`team-chart-${index}`} reference={reference} /> : null;
              })}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
};
