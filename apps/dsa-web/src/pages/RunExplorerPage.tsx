import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  Activity,
  ArrowLeft,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Database,
  RefreshCw,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
  ListChecks,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import {
  runExplorerApi,
  type AgentQualitySummary,
  type AgentRunDetail,
  type AgentRunSummary,
} from '../api/runExplorer';
import { Badge, Card } from '../components/common';
import { toApiErrorMessage } from '../api/error';
import { formatDateTime } from '../utils/format';
import { cn } from '../utils/cn';

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '全部' },
  { value: 'running', label: '运行中' },
  { value: 'interrupted', label: '等待审批' },
  { value: 'completed', label: '已完成' },
  { value: 'partial', label: '部分完成' },
  { value: 'blocked', label: '已阻止' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
];

const STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  recovering: '恢复中',
  interrupted: '等待审批',
  completed: '已完成',
  partial: '部分完成',
  failed: '失败',
  cancelled: '已取消',
  blocked: '已阻止',
  succeeded: '已完成',
};

const DIMENSION_LABELS: Record<string, string> = {
  control_loop: '控制循环',
  controlLoop: '控制循环',
  execution: '执行完整性',
  claim_evidence: '结论与证据',
  claimEvidence: '结论与证据',
  answer_contract: '回答契约',
  answerContract: '回答契约',
  budget: '资源预算',
};

const statusVariant = (status: string) => {
  if (status === 'completed' || status === 'succeeded') return 'success' as const;
  if (status === 'running' || status === 'recovering' || status === 'queued') return 'info' as const;
  if (status === 'partial' || status === 'blocked' || status === 'interrupted') return 'warning' as const;
  return 'danger' as const;
};

const formatDuration = (durationMs?: number | null) => {
  if (durationMs == null) return '—';
  if (durationMs < 1000) return `${durationMs} ms`;
  if (durationMs < 60_000) return `${(durationMs / 1000).toFixed(1)} 秒`;
  return `${Math.floor(durationMs / 60_000)} 分 ${Math.round((durationMs % 60_000) / 1000)} 秒`;
};

const percent = (value?: number | null) => (
  value == null ? '—' : `${Math.round(value * 100)}%`
);

const text = (value: unknown) => (
  typeof value === 'string' ? value : value == null ? '' : String(value)
);

const numberValue = (value: unknown) => (
  typeof value === 'number' ? value : Number(value || 0)
);

const RunExplorerPage: React.FC = () => {
  const [runs, setRuns] = useState<AgentRunSummary[]>([]);
  const [summary, setSummary] = useState<AgentQualitySummary | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AgentRunDetail | null>(null);
  const [status, setStatus] = useState('');
  const [tool, setTool] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const detailRequestId = useRef(0);
  const limit = 30;

  const loadDetail = useCallback(async (runId: string) => {
    const requestId = detailRequestId.current + 1;
    detailRequestId.current = requestId;
    setDetailLoading(true);
    try {
      const next = await runExplorerApi.getRun(runId);
      if (detailRequestId.current === requestId) {
        setDetail(next);
      }
    } catch (requestError) {
      if (detailRequestId.current === requestId) {
        setError(toApiErrorMessage(requestError, '运行详情加载失败'));
      }
    } finally {
      if (detailRequestId.current === requestId) {
        setDetailLoading(false);
      }
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [runResponse, qualityResponse] = await Promise.all([
        runExplorerApi.listRuns({
          status: status || undefined,
          tool: tool || undefined,
          page,
          limit,
        }),
        runExplorerApi.getQualitySummary(30),
      ]);
      setRuns(runResponse.items);
      setTotal(runResponse.total);
      setSummary(qualityResponse);
      setSelectedRunId((current) => (
        runResponse.items.some((item) => item.runId === current)
          ? current
          : runResponse.items[0]?.runId ?? null
      ));
      if (runResponse.items.length === 0) {
        setDetail(null);
      }
    } catch (requestError) {
      setError(toApiErrorMessage(requestError, '运行记录加载失败'));
    } finally {
      setLoading(false);
    }
  }, [page, status, tool]);

  useEffect(() => {
    document.title = '运行记录 - Stock Assistant';
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (selectedRunId) {
      void loadDetail(selectedRunId);
    } else {
      detailRequestId.current += 1;
      setDetailLoading(false);
      setDetail(null);
    }
  }, [loadDetail, selectedRunId]);

  const availableTools = useMemo(
    () => Array.from(new Set(runs.flatMap((run) => run.tools))).sort(),
    [runs],
  );
  const pageCount = Math.max(1, Math.ceil(total / limit));
  const run = detail?.snapshot.run ?? {};
  const projection = detail?.snapshot.qualityProjection ?? {};
  const actions = projection.actions ?? [];
  const toolResults = projection.toolResults ?? [];
  const evidence = projection.evidence ?? [];

  const selectRun = (runId: string) => {
    setSelectedRunId(runId);
  };

  const saveFeedback = async (rating: -1 | 1) => {
    if (!selectedRunId) return;
    try {
      const feedback = await runExplorerApi.saveFeedback(
        selectedRunId,
        rating,
        rating === 1 ? 'correctness' : 'other',
      );
      setDetail((current) => (
        current
          ? {
              ...current,
              snapshot: { ...current.snapshot, feedback },
            }
          : current
      ));
      setRuns((current) => current.map((item) => (
        item.runId === selectedRunId ? { ...item, feedback } : item
      )));
    } catch (requestError) {
      setError(toApiErrorMessage(requestError, '反馈保存失败'));
    }
  };

  return (
    <div className="mx-auto min-h-full max-w-[1500px] space-y-5 py-6">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <Link
            to="/"
            viewTransition
            className="mb-3 inline-flex items-center gap-1 text-sm text-secondary-text transition hover:text-foreground"
          >
            <ArrowLeft className="size-4" />
            返回助手
          </Link>
          <h1 className="text-2xl font-semibold text-foreground">运行记录</h1>
          <p className="mt-1 text-sm text-secondary-text">
            查看每次任务的动态行动、原子工具、证据校验、质量评分与反馈。
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="btn-secondary inline-flex items-center justify-center gap-2"
        >
          <RefreshCw className={cn('size-4', loading && 'animate-spin')} />
          刷新
        </button>
      </header>

      {error ? (
        <div className="rounded-xl border border-danger/20 bg-danger/8 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      ) : null}

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card padding="sm">
          <p className="text-xs text-secondary-text">近 30 天运行</p>
          <p className="mt-2 text-2xl font-semibold">{summary?.terminalRuns ?? '—'}</p>
        </Card>
        <Card padding="sm">
          <p className="text-xs text-secondary-text">平均质量分</p>
          <p className="mt-2 text-2xl font-semibold">{percent(summary?.quality.averageScore)}</p>
        </Card>
        <Card padding="sm">
          <p className="text-xs text-secondary-text">质量通过率</p>
          <p className="mt-2 text-2xl font-semibold">{percent(summary?.quality.passRate)}</p>
        </Card>
        <Card padding="sm">
          <p className="text-xs text-secondary-text">正向反馈率</p>
          <p className="mt-2 text-2xl font-semibold">{percent(summary?.feedback.positiveRate)}</p>
        </Card>
      </section>

      <section className="flex flex-wrap gap-2 rounded-2xl border border-border bg-card p-3">
        <div className="flex flex-wrap gap-1.5">
          {STATUS_OPTIONS.map((option) => (
            <button
              key={option.value || 'all'}
              type="button"
              onClick={() => {
                setStatus(option.value);
                setPage(1);
              }}
              className={cn(
                'rounded-lg px-3 py-1.5 text-xs font-medium transition',
                status === option.value
                  ? 'bg-foreground text-background'
                  : 'bg-muted text-secondary-text hover:text-foreground',
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
        <select
          value={tool}
          onChange={(event) => {
            setTool(event.target.value);
            setPage(1);
          }}
          className="ml-auto min-w-40 rounded-lg border border-border bg-background px-3 py-1.5 text-xs text-foreground outline-none focus:border-cyan"
          aria-label="按工具筛选"
        >
          <option value="">全部工具</option>
          {availableTools.map((item) => (
            <option key={item} value={item}>{item}</option>
          ))}
        </select>
      </section>

      <div className="grid min-h-[620px] gap-4 lg:grid-cols-[minmax(320px,0.8fr)_minmax(0,1.6fr)]">
        <Card padding="none" className="overflow-hidden">
          <div className="border-b border-border px-4 py-3">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold">任务列表</h2>
              <span className="text-xs text-secondary-text">{total} 条</span>
            </div>
          </div>
          <div className="max-h-[720px] divide-y divide-border/70 overflow-y-auto">
            {loading && runs.length === 0 ? (
              <div className="p-8 text-center text-sm text-secondary-text">正在加载运行记录…</div>
            ) : null}
            {!loading && runs.length === 0 ? (
              <div className="p-8 text-center text-sm text-secondary-text">当前筛选下没有运行记录。</div>
            ) : null}
            {runs.map((item) => (
              <button
                key={item.runId}
                type="button"
                onClick={() => selectRun(item.runId)}
                className={cn(
                  'block w-full px-4 py-3 text-left transition hover:bg-muted/60',
                  selectedRunId === item.runId && 'bg-cyan/8',
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge variant={statusVariant(item.status)}>
                        {STATUS_LABELS[item.status] ?? item.status}
                      </Badge>
                      <span className="text-xs font-medium text-foreground">
                        质量 {percent(item.qualityScore)}
                      </span>
                    </div>
                    <p className="mt-2 line-clamp-2 text-sm text-foreground/85">
                      {item.finalTextPreview || '尚未生成最终回答'}
                    </p>
                  </div>
                  <span className="shrink-0 text-[11px] text-secondary-text">
                    {formatDateTime(item.createdAt)}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {item.tools.slice(0, 4).map((itemTool) => (
                    <span
                      key={itemTool}
                      className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-secondary-text"
                    >
                      {itemTool}
                    </span>
                  ))}
                </div>
                <div className="mt-2 flex gap-3 text-[11px] text-secondary-text">
                  <span>{item.actionCount} 个动态动作</span>
                  <span>{item.evidenceCount} 条证据</span>
                  <span>{item.toolCallCount} 次工具调用</span>
                  <span>{formatDuration(item.durationMs)}</span>
                </div>
              </button>
            ))}
          </div>
          <div className="flex items-center justify-between border-t border-border px-3 py-2">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              className="rounded-md p-1 text-secondary-text transition hover:bg-muted disabled:opacity-30"
              aria-label="上一页"
            >
              <ChevronLeft className="size-4" />
            </button>
            <span className="text-xs text-secondary-text">{page} / {pageCount}</span>
            <button
              type="button"
              disabled={page >= pageCount}
              onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
              className="rounded-md p-1 text-secondary-text transition hover:bg-muted disabled:opacity-30"
              aria-label="下一页"
            >
              <ChevronRight className="size-4" />
            </button>
          </div>
        </Card>

        <div className="min-w-0 space-y-4">
          {!selectedRunId ? (
            <Card className="flex min-h-64 items-center justify-center text-sm text-secondary-text">
              选择一条运行记录查看详情。
            </Card>
          ) : null}
          {selectedRunId && detailLoading && !detail ? (
            <Card className="flex min-h-64 items-center justify-center text-sm text-secondary-text">
              正在加载运行详情…
            </Card>
          ) : null}
          {detail ? (
            <>
              <Card>
                <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={statusVariant(text(run.status))} size="md">
                        {STATUS_LABELS[text(run.status)] ?? text(run.status)}
                      </Badge>
                      <span className="font-mono text-xs text-secondary-text">{text(run.runId)}</span>
                    </div>
                    <p className="mt-3 text-sm text-secondary-text">
                      创建于 {formatDateTime(text(run.createdAt))}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-secondary-text">这次结果有帮助吗？</span>
                    <button
                      type="button"
                      onClick={() => void saveFeedback(1)}
                      className={cn(
                        'rounded-lg border p-2 transition',
                        detail.snapshot.feedback?.rating === 1
                          ? 'border-success/40 bg-success/10 text-success'
                          : 'border-border text-secondary-text hover:text-success',
                      )}
                      aria-label="有帮助"
                    >
                      <ThumbsUp className="size-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => void saveFeedback(-1)}
                      className={cn(
                        'rounded-lg border p-2 transition',
                        detail.snapshot.feedback?.rating === -1
                          ? 'border-danger/40 bg-danger/10 text-danger'
                          : 'border-border text-secondary-text hover:text-danger',
                      )}
                      aria-label="没帮助"
                    >
                      <ThumbsDown className="size-4" />
                    </button>
                  </div>
                </div>

                <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <div className="rounded-xl bg-muted/60 p-3">
                    <Clock3 className="size-4 text-cyan" />
                    <p className="mt-2 text-xs text-secondary-text">耗时</p>
                    <p className="mt-1 font-medium">{formatDuration(
                      text(run.startedAt) && text(run.finishedAt)
                        ? new Date(text(run.finishedAt)).getTime() - new Date(text(run.startedAt)).getTime()
                        : null,
                    )}</p>
                  </div>
                  <div className="rounded-xl bg-muted/60 p-3">
                    <ListChecks className="size-4 text-purple" />
                    <p className="mt-2 text-xs text-secondary-text">动态动作</p>
                    <p className="mt-1 font-medium">{actions.length}</p>
                  </div>
                  <div className="rounded-xl bg-muted/60 p-3">
                    <Database className="size-4 text-emerald-600" />
                    <p className="mt-2 text-xs text-secondary-text">工具调用</p>
                    <p className="mt-1 font-medium">{numberValue(run.toolCallCount)}</p>
                  </div>
                  <div className="rounded-xl bg-muted/60 p-3">
                    <Activity className="size-4 text-warning" />
                    <p className="mt-2 text-xs text-secondary-text">质量分</p>
                    <p className="mt-1 font-medium">{percent(detail.score.totalScore)}</p>
                  </div>
                </div>
              </Card>

              <Card title="质量检查" subtitle="Deterministic evaluation">
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {Object.entries(detail.score.dimensions).map(([name, dimension]) => (
                    <div key={name} className="rounded-xl border border-border/70 p-3">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium">{DIMENSION_LABELS[name] ?? name}</span>
                        <span className={cn(
                          'text-sm font-semibold',
                          dimension.score >= 0.85 ? 'text-success' : 'text-warning',
                        )}>
                          {percent(dimension.score)}
                        </span>
                      </div>
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
                        <div
                          className={cn(
                            'h-full rounded-full',
                            dimension.score >= 0.85 ? 'bg-success' : 'bg-warning',
                          )}
                          style={{ width: `${Math.round(dimension.score * 100)}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
                {detail.score.violations.length > 0 ? (
                  <div className="mt-4 space-y-2">
                    {detail.score.violations.map((violation, index) => (
                      <div
                        key={`${violation.code}-${index}`}
                        className="flex items-start gap-2 rounded-lg bg-warning/8 px-3 py-2 text-sm text-warning"
                      >
                        <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                        <span>{violation.code}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="mt-4 flex items-center gap-2 text-sm text-success">
                    <CheckCircle2 className="size-4" />
                    没有发现质量契约违规
                  </div>
                )}
              </Card>

              <Card title="执行链" subtitle="Dynamic actions → observations → evidence">
                <div className="space-y-3">
                  {actions.map((action, index) => {
                    const actionId = text(action.actionId);
                    const result = toolResults.find((item) => text(item.actionId) === actionId);
                    const actionEvidence = evidence.filter((item) => text(item.actionId) === actionId);
                    return (
                      <div key={actionId || index} className="rounded-xl border border-border/70 p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div>
                            <p className="text-sm font-medium">
                              {text(action.toolName) || '未指定工具'}
                            </p>
                            <p className="mt-0.5 text-xs text-secondary-text">{text(action.objective)}</p>
                            <p className="mt-0.5 font-mono text-[11px] text-secondary-text">{actionId}</p>
                          </div>
                          <Badge variant={result?.success === true ? 'success' : result ? 'danger' : 'info'}>
                            {result?.success === true ? '成功' : result ? '失败' : '未执行'}
                          </Badge>
                        </div>
                        <div className="mt-3 flex flex-wrap gap-4 text-xs text-secondary-text">
                          <span>副作用：{text(result?.effect) || '未执行'}</span>
                          <span>证据：{actionEvidence.length} 条</span>
                          <span>依赖：{Array.isArray(action.dependsOn) ? action.dependsOn.length : 0} 个</span>
                        </div>
                      </div>
                    );
                  })}
                  {actions.length === 0 ? (
                    <p className="text-sm text-secondary-text">该问题无需工具，或运行尚未生成行动。</p>
                  ) : null}
                </div>
              </Card>

              <Card title="最终回答" subtitle="Answer snapshot">
                <div className="whitespace-pre-wrap text-sm leading-7 text-foreground/85">
                  {text(run.finalText) || '尚未生成最终回答。'}
                </div>
              </Card>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
};

export default RunExplorerPage;
