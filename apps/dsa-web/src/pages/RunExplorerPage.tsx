import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { ArrowLeft, ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  runExplorerApi,
  type AgentQualitySummary,
  type AgentRunDetail,
  type AgentRunSummary,
  type AgentSourceSampleResponse,
} from '../api/runExplorer';
import { Badge, Card, CompactSelect, Modal } from '../components/common';
import { toApiErrorMessage } from '../api/error';
import { formatDateTime } from '../utils/format';
import { cn } from '../utils/cn';
import { RunDetailContent } from '../components/runExplorer/RunDetailContent';

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '全部' },
  { value: 'queued', label: '排队中' },
  { value: 'running', label: '运行中' },
  { value: 'recovering', label: '恢复中' },
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

const numberValue = (value: unknown) => (
  typeof value === 'number' ? value : Number(value || 0)
);

interface RunExplorerPageProps {
  embedded?: boolean;
}

const RunExplorerPage: React.FC<RunExplorerPageProps> = ({ embedded = false }) => {
  const [searchParams] = useSearchParams();
  const [runs, setRuns] = useState<AgentRunSummary[]>([]);
  const [summary, setSummary] = useState<AgentQualitySummary | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<AgentRunDetail | null>(null);
  const [status, setStatus] = useState(() => searchParams.get('status') ?? '');
  const [tool, setTool] = useState(() => searchParams.get('tool') ?? '');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailPayloadsLoading, setDetailPayloadsLoading] = useState(false);
  const [detailPayloadsLoaded, setDetailPayloadsLoaded] = useState(false);
  const [sourceSampling, setSourceSampling] = useState(false);
  const [sourceSample, setSourceSample] = useState<AgentSourceSampleResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const detailRequestId = useRef(0);
  const limit = 30;

  const loadDetail = useCallback(async (runId: string) => {
    const requestId = detailRequestId.current + 1;
    detailRequestId.current = requestId;
    setDetailLoading(true);
    setDetailPayloadsLoading(false);
    setDetailPayloadsLoaded(false);
    setSourceSampling(false);
    setSourceSample(null);
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

  const loadDetailPayloads = useCallback(async () => {
    if (!selectedRunId || detailPayloadsLoaded || detailPayloadsLoading) return;
    const requestId = detailRequestId.current + 1;
    detailRequestId.current = requestId;
    setDetailPayloadsLoading(true);
    try {
      const next = await runExplorerApi.getRun(selectedRunId, { includePayloads: true });
      if (detailRequestId.current === requestId) {
        setDetail(next);
        setDetailPayloadsLoaded(true);
      }
    } catch (requestError) {
      if (detailRequestId.current === requestId) {
        setError(toApiErrorMessage(requestError, '工具请求与返回加载失败'));
      }
    } finally {
      if (detailRequestId.current === requestId) {
        setDetailPayloadsLoading(false);
      }
    }
  }, [detailPayloadsLoaded, detailPayloadsLoading, selectedRunId]);

  const sampleSources = useCallback(async () => {
    if (!selectedRunId || sourceSampling) return;
    setSourceSampling(true);
    setError(null);
    try {
      const sampled = await runExplorerApi.sampleSources(selectedRunId, 3);
      setSourceSample(sampled);
    } catch (requestError) {
      setError(toApiErrorMessage(requestError, '来源自动抽检失败'));
    } finally {
      setSourceSampling(false);
    }
  }, [selectedRunId, sourceSampling]);

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
      setDetailPayloadsLoading(false);
      setDetailPayloadsLoaded(false);
      setDetailLoading(false);
      setDetail(null);
      setSourceSampling(false);
      setSourceSample(null);
    }
  }, [loadDetail, selectedRunId]);

  const availableTools = useMemo(
    () => Array.from(new Set(runs.flatMap((run) => run.tools))).sort(),
    [runs],
  );
  const pageCount = Math.max(1, Math.ceil(total / limit));
  const violationCount = summary
    ? Object.values(summary.quality.violations).reduce((totalCount, count) => totalCount + numberValue(count), 0)
    : null;
  const behaviorReviewRuns = summary?.behavior
    ? summary.behavior.warningRuns + summary.behavior.dangerRuns
    : null;
  const behaviorInfoRuns = summary?.behavior?.infoRuns ?? null;

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
          <p className="text-xs text-secondary-text">规则核对通过</p>
          <p className="mt-0.5 text-xl font-semibold leading-6">
            {summary ? `${summary.quality.passedRuns}/${summary.quality.scoredRuns}` : '—'}
          </p>
        </Card>
        <Card padding="none" className="rounded-xl px-3 py-2.5">
          <p className="text-xs text-secondary-text">质量契约问题</p>
          <p className="mt-0.5 text-xl font-semibold leading-6">{violationCount ?? '—'}</p>
          {behaviorReviewRuns != null ? <p className="mt-0.5 text-[10px] text-warning">执行诊断发现待处理问题 {behaviorReviewRuns} 条运行</p> : null}
          {behaviorInfoRuns ? <p className="mt-0.5 text-[10px] text-cyan">另有观察提示运行 {behaviorInfoRuns} 条</p> : null}
        </Card>
        <Card padding="none" className="rounded-xl px-3 py-2.5">
          <p className="text-xs text-secondary-text">平均规则核对分</p>
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
                        规则核对 {percent(item.qualityScore)}
                      </span>
                      {item.unreadDocumentCount ? <Badge variant="info">候选文档未选取 {item.unreadDocumentCount}</Badge> : null}
                      {item.unreadArticleCount ? <Badge variant="info">候选文章未选取 {item.unreadArticleCount}</Badge> : null}
                      {(item.behaviorActionRequiredCount ?? ((item.behaviorWarningCount ?? 0) + (item.behaviorDangerCount ?? 0))) > 0 ? (
                        <Badge variant={(item.behaviorDangerCount ?? 0) > 0 ? 'danger' : 'warning'}>
                          需要处理 {(item.behaviorActionRequiredCount ?? ((item.behaviorWarningCount ?? 0) + (item.behaviorDangerCount ?? 0)))}
                        </Badge>
                      ) : (item.behaviorAdvisoryCount ?? item.behaviorInfoCount ?? 0) ? (
                        <Badge variant="info">观察提示 {(item.behaviorAdvisoryCount ?? item.behaviorInfoCount ?? 0)}</Badge>
                      ) : null}
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
                  <span>{item.toolCallCount} 次工具调用</span>
                  {item.contentReadCallCount != null ? <span>正文读取 {item.contentReadCallCount} 次</span> : null}
                  {item.failedToolCount ? <span className="text-danger">失败尝试 {item.failedToolCount} 次</span> : null}
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
            <RunDetailContent
              detail={detail}
              onLoadToolPayloads={() => { void loadDetailPayloads(); }}
              toolPayloadsLoading={detailPayloadsLoading}
              toolPayloadsLoaded={detailPayloadsLoaded}
              onSampleSources={() => { void sampleSources(); }}
              sourceSampling={sourceSampling}
              sourceSample={sourceSample}
              onFeedback={(rating) => { void saveFeedback(rating); }}
            />
          ) : null}
        </Modal>
      </div>
    </div>
  );
};

export default RunExplorerPage;
