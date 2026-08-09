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
import { Badge, Card, CompactSelect, Modal } from '../components/common';
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
  control_loop: '分析过程',
  controlLoop: '分析过程',
  execution: '执行完成',
  evidence_links: '资料对应',
  evidenceLinks: '资料对应',
  answer_contract: '回答完整度',
  answerContract: '回答完整度',
  budget: '运行资源',
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

const stringList = (value: unknown): string[] => (
  Array.isArray(value)
    ? value.map((item) => text(item)).filter(Boolean)
    : []
);

const uniqueStrings = (values: string[]) => Array.from(new Set(values));

const isHttpUrl = (value: string) => /^https?:\/\//i.test(value);

const sourceLabel = (value: string) => {
  const trimmed = value.trim();
  if (!isHttpUrl(trimmed)) return trimmed.replace(/^www\./i, '');
  try {
    return new URL(trimmed).hostname.replace(/^www\./i, '');
  } catch {
    return trimmed;
  }
};

const displayDataTime = (value: unknown) => {
  const valueText = text(value);
  return valueText ? formatDateTime(valueText) : '时间未提供';
};

const violationLabel = (code: string) => {
  const labels: Record<string, string> = {
    execution_contract_failed: '部分执行步骤没有完整结束',
    evidence_link_contract_failed: '部分结论没有完成逐条资料对应',
    answer_contract_failed: '回答内容没有完全满足要求',
    control_loop_contract_failed: '分析过程存在异常或缺少步骤',
    budget_contract_failed: '本次分析触及运行资源上限',
  };
  return labels[code] ?? code.replaceAll('_', ' ');
};

interface RunExplorerPageProps {
  embedded?: boolean;
}

const RunExplorerPage: React.FC<RunExplorerPageProps> = ({ embedded = false }) => {
  const [runs, setRuns] = useState<AgentRunSummary[]>([]);
  const [summary, setSummary] = useState<AgentQualitySummary | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
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
    setDetail(null);
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
    document.title = '分析记录 - Stock Assistant';
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
  const toolResults = projection.toolResults ?? [];
  const evidence = projection.evidence ?? [];
  const toolNames = uniqueStrings(
    toolResults.map((item) => text(item.toolName)).filter(Boolean),
  );
  const sourceNames = uniqueStrings(
    evidence.flatMap((item) => stringList(item.sourceRefs)),
  );
  const sourceLabels = sourceNames.length > 0
    ? uniqueStrings(sourceNames.map(sourceLabel))
    : toolNames;
  const violationCount = summary
    ? Object.values(summary.quality.violations).reduce((totalCount, count) => totalCount + numberValue(count), 0)
    : null;

  const selectRun = (runId: string) => {
    setSelectedRunId(runId);
    setDetailOpen(true);
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
    <div className={cn('min-h-full space-y-3', !embedded && 'mx-auto max-w-[1500px] py-4')}>
      <header className="flex items-center justify-between gap-3 border-b border-border/70 pb-3">
        <div>
          {!embedded ? (
            <Link
              to="/"
              viewTransition
              className="inline-flex items-center gap-1 text-xs text-secondary-text transition hover:text-foreground"
            >
              <ArrowLeft className="size-3.5" />
              返回助手
            </Link>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="btn-secondary inline-flex h-8 shrink-0 items-center justify-center gap-1.5 px-2.5 text-xs"
        >
          <RefreshCw className={cn('size-3.5', loading && 'animate-spin')} />
          刷新
        </button>
      </header>

      {error ? (
        <div className="rounded-lg border border-danger/20 bg-danger/8 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      ) : null}

      <section className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Card padding="none" className="rounded-xl px-3 py-2.5">
          <p className="text-xs text-secondary-text">近 30 天分析</p>
          <p className="mt-0.5 text-xl font-semibold leading-6">{summary?.terminalRuns ?? '—'}</p>
        </Card>
        <Card padding="none" className="rounded-xl px-3 py-2.5">
          <p className="text-xs text-secondary-text">核对通过</p>
          <p className="mt-0.5 text-xl font-semibold leading-6">
            {summary ? `${summary.quality.passedRuns}/${summary.quality.scoredRuns}` : '—'}
          </p>
        </Card>
        <Card padding="none" className="rounded-xl px-3 py-2.5">
          <p className="text-xs text-secondary-text">待核对问题</p>
          <p className="mt-0.5 text-xl font-semibold leading-6">{violationCount ?? '—'}</p>
        </Card>
        <Card padding="none" className="rounded-xl px-3 py-2.5">
          <p className="text-xs text-secondary-text">平均核对分</p>
          <p className="mt-0.5 text-xl font-semibold leading-6">{percent(summary?.quality.averageScore)}</p>
        </Card>
      </section>

      <section className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card p-2">
        <div className="w-full">
          <div className="mb-1.5 flex items-center gap-2">
            <span className="text-xs font-semibold text-foreground">筛选分析记录</span>
            <span className="text-[11px] text-secondary-text">按结果状态或使用的资料入口查找</span>
          </div>
          <div className="flex min-w-0 flex-wrap gap-1">
            {STATUS_OPTIONS.map((option) => (
              <button
                key={option.value || 'all'}
                type="button"
                onClick={() => {
                  setStatus(option.value);
                  setPage(1);
                }}
                className={cn(
                  'rounded-md px-2.5 py-1 text-[11px] font-medium transition',
                  status === option.value
                    ? 'bg-foreground text-background'
                    : 'bg-muted text-secondary-text hover:text-foreground',
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        <CompactSelect
          value={tool}
          onChange={(value) => {
            setTool(value);
            setPage(1);
          }}
          options={[
            { value: '', label: '全部工具' },
            ...availableTools.map((item) => ({ value: item, label: item })),
          ]}
          ariaLabel="按工具筛选"
          className="w-full sm:w-44"
        />
      </section>

      <div>
        <Card padding="none" className="overflow-hidden rounded-xl">
          <div className="border-b border-border px-3 py-2">
            <div className="flex items-end justify-between gap-2">
              <div>
                <h2 className="text-sm font-semibold">最近分析</h2>
                <p className="mt-0.5 text-[11px] text-secondary-text">选择一条查看回答、来源和核对结果</p>
              </div>
              <span className="shrink-0 text-xs text-secondary-text">{total} 条</span>
            </div>
          </div>
          <div className="max-h-[420px] divide-y divide-border/70 overflow-y-auto">
            {loading && runs.length === 0 ? (
              <div className="p-5 text-center text-xs text-secondary-text">正在加载运行记录…</div>
            ) : null}
            {!loading && runs.length === 0 ? (
              <div className="p-5 text-center text-xs text-secondary-text">当前筛选下没有运行记录。</div>
            ) : null}
            {runs.map((item) => (
              <button
                key={item.runId}
                type="button"
                onClick={() => selectRun(item.runId)}
                className={cn(
                  'block w-full px-3 py-2 text-left transition hover:bg-muted/60',
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
                        核对 {percent(item.qualityScore)}
                      </span>
                    </div>
                    <p className="mt-1 line-clamp-1 text-xs text-foreground/85">
                      {item.finalTextPreview || '尚未生成最终回答'}
                    </p>
                  </div>
                  <span className="shrink-0 text-[11px] text-secondary-text">
                    {formatDateTime(item.createdAt)}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {item.tools.slice(0, 4).map((itemTool) => (
                    <span
                      key={itemTool}
                      className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-secondary-text"
                    >
                      {itemTool}
                    </span>
                  ))}
                </div>
                <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px] text-secondary-text">
                  <span>{item.toolObservationCount} 条资料返回</span>
                  <span>{item.evidenceCount} 条证据</span>
                  <span>{item.toolCallCount} 个资料入口</span>
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

        <Modal
          isOpen={detailOpen}
          onClose={() => setDetailOpen(false)}
          title="本次分析"
          width="max-w-4xl"
        >
          {selectedRunId && detailLoading && !detail ? (
            <div className="flex min-h-32 items-center justify-center rounded-lg border border-border/70 p-3 text-xs text-secondary-text">
              正在加载运行详情…
            </div>
          ) : null}
          {selectedRunId && !detailLoading && !detail && error ? (
            <div className="rounded-lg border border-danger/20 bg-danger/8 px-3 py-2 text-xs text-danger">
              {error}
            </div>
          ) : null}
          {detail ? (
            <div className="max-h-[calc(100vh-9rem)] space-y-3 overflow-y-auto pr-1">
              <Card padding="none" className="rounded-xl p-3">
                <div className="flex flex-col gap-2.5 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <h2 className="text-sm font-semibold text-foreground">回答结果</h2>
                      <Badge variant={statusVariant(text(run.status))}>
                        {STATUS_LABELS[text(run.status)] ?? text(run.status)}
                      </Badge>
                    </div>
                    <p className="mt-1.5 text-xs text-secondary-text">
                      生成于 {formatDateTime(text(run.createdAt))}
                    </p>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs text-secondary-text">这次结果有帮助吗？</span>
                    <button
                      type="button"
                      onClick={() => void saveFeedback(1)}
                      className={cn(
                        'rounded-md border p-1.5 transition',
                        detail.snapshot.feedback?.rating === 1
                          ? 'border-success/40 bg-success/10 text-success'
                          : 'border-border text-secondary-text hover:text-success',
                      )}
                      aria-label="有帮助"
                    >
                      <ThumbsUp className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => void saveFeedback(-1)}
                      className={cn(
                        'rounded-md border p-1.5 transition',
                        detail.snapshot.feedback?.rating === -1
                          ? 'border-danger/40 bg-danger/10 text-danger'
                          : 'border-border text-secondary-text hover:text-danger',
                      )}
                      aria-label="没帮助"
                    >
                      <ThumbsDown className="size-3.5" />
                    </button>
                  </div>
                </div>

                <div className="mt-3 rounded-lg border border-border/70 bg-muted/35 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-xs font-semibold text-foreground">助手给出的结论</p>
                    <span className={cn(
                      'text-[11px] font-medium',
                      detail.score.passed ? 'text-success' : 'text-warning',
                    )}>
                      {detail.score.passed ? '资料核对通过' : `有 ${detail.score.violations.length} 个待核对问题`}
                    </span>
                  </div>
                  <div className="mt-1.5 max-h-56 overflow-y-auto whitespace-pre-wrap text-xs leading-6 text-foreground/85">
                    {text(run.finalText) || '尚未生成最终回答。'}
                  </div>
                </div>

                <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <div className="rounded-lg bg-muted/60 p-2.5">
                    <Clock3 className="size-3.5 text-cyan" />
                    <p className="mt-1 text-[11px] text-secondary-text">耗时</p>
                    <p className="mt-0.5 text-sm font-medium">{formatDuration(
                      text(run.startedAt) && text(run.finishedAt)
                        ? new Date(text(run.finishedAt)).getTime() - new Date(text(run.startedAt)).getTime()
                        : null,
                    )}</p>
                  </div>
                  <div className="rounded-lg bg-muted/60 p-2.5">
                    <ListChecks className="size-3.5 text-purple" />
                    <p className="mt-1 text-[11px] text-secondary-text">资料返回</p>
                    <p className="mt-0.5 text-sm font-medium">{toolResults.length}</p>
                  </div>
                  <div className="rounded-lg bg-muted/60 p-2.5">
                    <Database className="size-3.5 text-emerald-600" />
                    <p className="mt-1 text-[11px] text-secondary-text">资料入口</p>
                    <p className="mt-0.5 text-sm font-medium">{toolNames.length}</p>
                  </div>
                  <div className="rounded-lg bg-muted/60 p-2.5">
                    <Activity className="size-3.5 text-warning" />
                    <p className="mt-1 text-[11px] text-secondary-text">核对分</p>
                    <p className="mt-0.5 text-sm font-medium">{percent(detail.score.totalScore)}</p>
                  </div>
                </div>
              </Card>

              <Card padding="none" className="rounded-xl p-3" title="参考资料" subtitle="回答依据">
                <p className="text-xs text-secondary-text">
                  本次回答关联 {sourceLabels.length} 个来源
                  {sourceNames.length > sourceLabels.length ? `（${sourceNames.length} 条原始引用）` : ''}
                  ，整理成 {evidence.length} 条可核对证据。
                </p>
                {sourceLabels.length > 0 ? (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {sourceLabels.map((source) => (
                      <span key={source} className="rounded-md bg-cyan/8 px-2 py-1 text-[11px] text-cyan" title={source}>
                        {source}
                      </span>
                    ))}
                  </div>
                ) : null}
                <div className="mt-2 divide-y divide-border/70 rounded-lg border border-border/70">
                  {evidence.map((item, index) => {
                    const itemSourceRefs = stringList(item.sourceRefs);
                    const itemSources = uniqueStrings(itemSourceRefs.map(sourceLabel));
                    const itemUrls = itemSourceRefs.filter(isHttpUrl);
                    const evidenceId = text(item.evidenceId) || text(item.id) || `证据 ${index + 1}`;
                    return (
                      <div key={evidenceId} className="px-2.5 py-2">
                        <div className="flex items-center justify-between gap-2">
                          <span
                            className="line-clamp-2 text-xs font-medium text-foreground"
                            title={itemSourceRefs.join('、')}
                          >
                            {itemSources.join('、') || text(item.toolName) || '未标注来源'}
                          </span>
                          <span className="shrink-0 text-[10px] text-secondary-text">
                            {displayDataTime(item.dataTime)}
                          </span>
                        </div>
                        <p className="mt-0.5 truncate font-mono text-[10px] text-secondary-text" title={evidenceId}>
                          {evidenceId}
                        </p>
                        {itemUrls.length > 0 ? (
                          <details className="mt-1.5 text-[10px] text-secondary-text">
                            <summary className="cursor-pointer select-none hover:text-foreground">
                              查看 {itemUrls.length} 条原始链接
                            </summary>
                            <div className="mt-1 space-y-0.5 border-l border-border pl-2">
                              {itemUrls.map((url) => (
                                <a
                                  key={url}
                                  href={url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="block break-all text-cyan hover:underline"
                                >
                                  {url}
                                </a>
                              ))}
                            </div>
                          </details>
                        ) : null}
                      </div>
                    );
                  })}
                  {evidence.length === 0 ? (
                    <p className="px-2.5 py-2 text-xs text-secondary-text">本次回答没有可展示的资料记录。</p>
                  ) : null}
                </div>
              </Card>

              <Card padding="none" className="rounded-xl p-3" title="结果核对" subtitle="自动检查回答与资料是否对应">
                <div className={cn(
                  'rounded-lg px-3 py-2',
                  detail.score.passed ? 'bg-success/8 text-success' : 'bg-warning/10 text-warning',
                )}>
                  <p className="text-xs font-semibold">
                    {detail.score.passed ? '这次回答的资料核对已通过' : '这次回答还有内容需要核对'}
                  </p>
                  <p className="mt-0.5 text-[11px] opacity-85">
                    {detail.score.passed
                      ? '回答中的事实已经找到对应的资料记录。'
                      : '下面列出未完全满足的检查项，阅读结论时请优先关注这些部分。'}
                  </p>
                </div>
                <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {Object.entries(detail.score.dimensions).map(([name, dimension]) => (
                    <div key={name} className="rounded-lg border border-border/70 p-2.5">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-medium">{DIMENSION_LABELS[name] ?? name}</span>
                        <span className={cn(
                          'text-xs font-semibold',
                          dimension.score >= 0.85 ? 'text-success' : 'text-warning',
                        )}>
                          {percent(dimension.score)}
                        </span>
                      </div>
                      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-muted">
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
                  <div className="mt-3 space-y-1.5">
                    {detail.score.violations.map((violation, index) => (
                      <div
                        key={`${violation.code}-${index}`}
                        className="flex items-start gap-2 rounded-md bg-warning/8 px-2.5 py-1.5 text-xs text-warning"
                      >
                        <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
                        <span>{violationLabel(violation.code)}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="mt-3 flex items-center gap-2 text-xs text-success">
                    <CheckCircle2 className="size-3.5" />
                    暂未发现明显的资料关联问题
                  </div>
                )}
              </Card>

              <Card padding="none" className="rounded-xl p-3" title="资料获取过程" subtitle="本次回答实际使用的资料入口">
                <div className="space-y-2">
                  {toolResults.map((result, index) => {
                    const actionId = text(result.actionId) || text(result.toolCallId);
                    const actionEvidence = evidence.filter((item) => text(item.actionId) === text(result.actionId));
                    const actionSourceRefs = uniqueStrings(
                      actionEvidence.flatMap((item) => stringList(item.sourceRefs)),
                    );
                    const actionSources = uniqueStrings(actionSourceRefs.map(sourceLabel));
                    return (
                      <div key={actionId || index} className="rounded-lg border border-border/70 p-2.5">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div>
                            <p className="text-xs font-medium">
                              {text(result.toolName) || '未指定工具'}
                            </p>
                            <p className="mt-0.5 truncate font-mono text-[10px] text-secondary-text" title={actionId}>
                              调用编号 {actionId}
                            </p>
                          </div>
                          <Badge variant={result.success === true ? 'success' : 'danger'}>
                            {result.success === true ? '成功' : '失败'}
                          </Badge>
                        </div>
                        <p
                          className="mt-2 line-clamp-2 text-[11px] text-foreground/75"
                          title={actionSourceRefs.join('、')}
                        >
                          {actionSources.join('、') || '来源未标注'} · {actionEvidence.length} 条证据
                          {actionSourceRefs.length > 0 ? ` · ${actionSourceRefs.length} 个来源` : ''}
                        </p>
                        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-secondary-text">
                          <span>类型：{text(result.effect) === 'side_effect' ? '外部操作' : '读取资料'}</span>
                          <span>数据时间：{displayDataTime(result.dataTime || actionEvidence[0]?.dataTime)}</span>
                        </div>
                      </div>
                    );
                  })}
                  {toolResults.length === 0 ? (
                    <p className="text-xs text-secondary-text">该问题没有调用工具。</p>
                  ) : null}
                </div>
              </Card>

            </div>
          ) : null}
        </Modal>
      </div>
    </div>
  );
};

export default RunExplorerPage;
