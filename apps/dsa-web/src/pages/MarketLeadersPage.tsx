import React, { useEffect, useMemo, useState } from 'react';
import { ChevronDown, Flame, Radio } from 'lucide-react';
import {
  marketThemesApi,
  type MarketMainlineReportResponse,
} from '../api/marketThemes';
import { analysisApi } from '../api/analysis';
import { useTaskStream } from '../hooks/useTaskStream';
import { Badge, Button, Card, InlineAlert, Loading } from '../components/common';

const ACTIVE_TASK_STORAGE_KEY = 'market-mainline-active-task-id';

interface MarketMainlineTaskPayload {
  phase?: string;
  stream_text?: string;
  report_draft?: Partial<MarketMainlineReportResponse>;
  report?: MarketMainlineReportResponse;
  llm_used?: boolean;
  model_used?: string | null;
  debug_input?: MarketMainlineReportResponse['debug_input'];
}

function hasReadyReport(report: MarketMainlineReportResponse | null | undefined): boolean {
  return Boolean(report) && report?.report_pending === false;
}

function readTaskPayload(value: unknown): MarketMainlineTaskPayload {
  if (!value || typeof value !== 'object') {
    return {};
  }
  const payload = value as Record<string, unknown>;
  return {
    phase: (payload.phase as string | undefined) ?? undefined,
    stream_text: (payload.stream_text as string | undefined) ?? (payload.streamText as string | undefined),
    report_draft:
      (payload.report_draft as Partial<MarketMainlineReportResponse> | undefined)
      ?? (payload.reportDraft as Partial<MarketMainlineReportResponse> | undefined),
    report:
      (payload.report as MarketMainlineReportResponse | undefined)
      ?? undefined,
    llm_used: (payload.llm_used as boolean | undefined) ?? (payload.llmUsed as boolean | undefined),
    model_used: (payload.model_used as string | null | undefined) ?? (payload.modelUsed as string | null | undefined),
    debug_input:
      (payload.debug_input as MarketMainlineReportResponse['debug_input'] | undefined)
      ?? (payload.debugInput as MarketMainlineReportResponse['debug_input'] | undefined),
  };
}

function mergeDraftIntoReport(
  base: MarketMainlineReportResponse | null,
  draft?: Partial<MarketMainlineReportResponse> | null,
  debugInput?: MarketMainlineReportResponse['debug_input'],
): MarketMainlineReportResponse | null {
  if (!base && !draft && !debugInput) return null;
  return {
    id: draft?.id ?? base?.id,
    report_key: draft?.report_key ?? base?.report_key,
    mode: draft?.mode ?? base?.mode,
    created_at: draft?.created_at ?? base?.created_at,
    generated_at: draft?.generated_at ?? base?.generated_at ?? '',
    as_of_date: draft?.as_of_date ?? base?.as_of_date ?? '',
    overview: draft?.overview ?? base?.overview ?? '',
    full_report: draft?.full_report ?? base?.full_report ?? '',
    market_stage: draft?.market_stage ?? base?.market_stage ?? { label: '', description: '' },
    current_mainlines: draft?.current_mainlines ?? base?.current_mainlines ?? [],
    future_mainlines: draft?.future_mainlines ?? base?.future_mainlines ?? [],
    action_summary: draft?.action_summary ?? base?.action_summary ?? [],
    evidence_digest: draft?.evidence_digest ?? base?.evidence_digest ?? { policy: [], industry: [], market: [] },
    source_summary: draft?.source_summary ?? base?.source_summary,
    llm_used: draft?.llm_used ?? base?.llm_used ?? false,
    model_used: draft?.model_used ?? base?.model_used ?? null,
    raw_stream_output: draft?.raw_stream_output ?? base?.raw_stream_output,
    raw_response: draft?.raw_response ?? base?.raw_response,
    report_pending: draft?.report_pending ?? base?.report_pending,
    _cached: draft?._cached ?? base?._cached,
    debug_input: debugInput ?? draft?.debug_input ?? base?.debug_input,
  };
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value ?? '');
  }
}

function detectStreamKind(text: string): 'json' | 'report' {
  const trimmed = text.trim();
  return trimmed.startsWith('{') || trimmed.startsWith('[') ? 'json' : 'report';
}

function formatStreamText(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) {
    return '';
  }

  if (detectStreamKind(trimmed) !== 'json') {
    return trimmed;
  }

  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return trimmed;
  }
}

function renderReportParagraphs(text: string): React.ReactNode {
  const normalized = text
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);

  const paragraphs = normalized.length > 0
    ? normalized
    : text
      .split('\n')
      .map((paragraph) => paragraph.trim())
      .filter(Boolean);

  if (paragraphs.length === 0) {
    return null;
  }

  return (
    <div className="space-y-5">
      {paragraphs.map((paragraph, index) => (
        <p key={`${index}-${paragraph.slice(0, 16)}`} className="market-stream-paragraph">
          {paragraph}
        </p>
      ))}
    </div>
  );
}

function hasStructuredContent(report: Partial<MarketMainlineReportResponse> | null | undefined): boolean {
  if (!report) {
    return false;
  }
  return Boolean(
    (report.overview && report.overview.trim())
    || (report.full_report && report.full_report.trim())
    || (report.current_mainlines && report.current_mainlines.length > 0)
    || (report.future_mainlines && report.future_mainlines.length > 0)
    || (report.action_summary && report.action_summary.length > 0)
  );
}

function mergeTaskDraft(
  previous: Partial<MarketMainlineReportResponse> | null,
  nextDraft?: Partial<MarketMainlineReportResponse> | null,
): Partial<MarketMainlineReportResponse> | null {
  if (!nextDraft) {
    return previous;
  }
  if (!previous) {
    return nextDraft;
  }

  const previousFullReport = previous.full_report || '';
  const nextFullReport = nextDraft.full_report || '';

  return {
    ...previous,
    ...nextDraft,
    full_report: nextFullReport.length >= previousFullReport.length ? nextFullReport : previousFullReport,
  };
}

const MarketLeadersPage: React.FC = () => {
  const [report, setReport] = useState<MarketMainlineReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [taskError, setTaskError] = useState<string | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [streamText, setStreamText] = useState('');
  const [reportDraft, setReportDraft] = useState<Partial<MarketMainlineReportResponse> | null>(null);
  const [debugInput, setDebugInput] = useState<MarketMainlineReportResponse['debug_input'] | undefined>(undefined);
  const [streamPhase, setStreamPhase] = useState<string | null>(null);
  const [taskMessage, setTaskMessage] = useState<string | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [showRawInput, setShowRawInput] = useState(false);
  const [showRawOutput, setShowRawOutput] = useState(false);

  const clearStreamingDisplay = () => {
    setReport(null);
    setStreamText('');
    setReportDraft(null);
    setDebugInput(undefined);
  };

  const updateStreamText = (nextText?: string) => {
    if (typeof nextText !== 'string') {
      return;
    }
    setStreamText((current) => (nextText.length >= current.length ? nextText : current));
  };

  const updateReportDraft = (nextDraft?: Partial<MarketMainlineReportResponse>) => {
    if (!nextDraft) {
      return;
    }
    setReportDraft((current) => mergeTaskDraft(current, nextDraft));
  };

  const rememberActiveTask = (taskId: string | null) => {
    if (taskId) {
      window.localStorage.setItem(ACTIVE_TASK_STORAGE_KEY, taskId);
    } else {
      window.localStorage.removeItem(ACTIVE_TASK_STORAGE_KEY);
    }
  };

  const loadLatest = async (): Promise<MarketMainlineReportResponse | null> => {
    setLoading(true);
    setError(null);
    try {
      const response = await marketThemesApi.getReport();
      if (hasReadyReport(response)) {
        setReport(response);
        setDebugInput(response.debug_input);
        setTaskError(null);
        setStreamPhase(null);
        setTaskMessage(null);
        setStreamText('');
        setReportDraft(null);
        clearActiveTask();
      } else {
        clearStreamingDisplay();
      }
      return response;
    } catch {
      setError('市场主线报告加载失败。');
      return null;
    } finally {
      setLoading(false);
    }
  };

  const applyTaskState = (
    payload: MarketMainlineTaskPayload,
    message: string | null,
    taskId: string,
    status: 'pending' | 'processing' | 'completed' | 'failed',
  ) => {
    if (status === 'pending' || status === 'processing') {
      setReport(null);
    }
    setActiveTaskId(taskId);
    rememberActiveTask(taskId);
    setIsGenerating(status === 'pending' || status === 'processing');
    setTaskMessage(message);
    setStreamPhase(payload.phase ?? (status === 'pending' ? 'queued' : status));
    updateStreamText(payload.stream_text);
    updateReportDraft(payload.report_draft);
    if (payload.debug_input) {
      setDebugInput(payload.debug_input);
    }
    if (payload.report) {
      setReport(payload.report);
      setDebugInput(payload.report.debug_input);
    }
  };

  const clearActiveTask = () => {
    setActiveTaskId(null);
    rememberActiveTask(null);
    setIsGenerating(false);
  };

  const hydrateTaskStatus = async (taskId: string): Promise<boolean> => {
    try {
      const status = await analysisApi.getStatus(taskId);
      const payload = readTaskPayload(status.result?.report);

      if (status.status === 'pending' || status.status === 'processing') {
        applyTaskState(
          payload,
          payload.phase ? `任务进行中：${payload.phase}` : '任务进行中',
          taskId,
          status.status,
        );
        return true;
      }

      if (status.status === 'completed') {
        if (payload.report) {
          setReport(payload.report);
          setDebugInput(payload.report.debug_input);
        }
        setTaskError(null);
        setStreamPhase('completed');
        setTaskMessage('模型研判生成完成');
        clearActiveTask();
        void loadLatest();
        return false;
      }

      setTaskError(status.error || '模型研判生成失败。');
      setStreamPhase('failed');
      setTaskMessage(status.error || '模型研判生成失败');
      clearActiveTask();
      return false;
    } catch {
      setTaskMessage('任务状态恢复中');
      return false;
    }
  };

  const resumeActiveTask = async (): Promise<boolean> => {
    const rememberedTaskId = window.localStorage.getItem(ACTIVE_TASK_STORAGE_KEY);
    if (rememberedTaskId) {
      setActiveTaskId(rememberedTaskId);
      setIsGenerating(true);
      setStreamPhase('reconnecting');
      setTaskMessage('正在恢复进行中的任务');
      const resumed = await hydrateTaskStatus(rememberedTaskId);
      if (resumed) {
        return true;
      }
    }

    try {
      const tasksResponse = await analysisApi.getTasks({ status: 'pending,processing', limit: 20 });
      const match = tasksResponse.tasks.find(
        (task) => task.stockCode === 'MARKET_MAINLINE' && task.reportType === 'market_mainline_report',
      );
      if (!match) {
        return false;
      }
      return hydrateTaskStatus(match.taskId);
    } catch {
      return false;
    }
  };

  const startGeneration = async (force: boolean = true) => {
    setSubmitting(true);
    setError(null);
    setTaskError(null);
    clearStreamingDisplay();
    setStreamPhase('queued');
    setTaskMessage('任务已提交，等待服务端开始处理');
    try {
      const task = await marketThemesApi.createReportTask(force);
      setActiveTaskId(task.task_id);
      rememberActiveTask(task.task_id);
      setIsGenerating(true);
    } catch (error) {
      const message = error instanceof Error && error.message ? error.message : '模型研判任务提交失败。';
      setTaskError(message);
      setIsGenerating(false);
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    let active = true;
    const initialize = async () => {
      const resumed = await resumeActiveTask();
      if (!active) {
        return;
      }
      if (resumed) {
        return;
      }
      const latest = await loadLatest();
      if (!active) {
        return;
      }
      if (hasReadyReport(latest)) {
        return;
      }
      if (latest?.report_pending === true) {
        void startGeneration(false);
      }
    };
    void initialize();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!activeTaskId) {
      return;
    }
    const intervalId = window.setInterval(() => {
      void hydrateTaskStatus(activeTaskId);
    }, 5000);
    return () => window.clearInterval(intervalId);
  }, [activeTaskId]);

  useTaskStream({
    enabled: Boolean(activeTaskId),
    onTaskStarted: (task) => {
      if (task.taskId !== activeTaskId) return;
      setIsGenerating(true);
      setStreamPhase('started');
      setTaskMessage(task.message || '任务已启动');
    },
    onTaskProgress: (task) => {
      if (task.taskId !== activeTaskId) return;
      const payload = readTaskPayload(task.result);
      setIsGenerating(true);
      setStreamPhase(payload.phase ?? 'generating');
      setTaskMessage(task.message || null);
      updateStreamText(payload.stream_text);
      updateReportDraft(payload.report_draft);
      if (payload.debug_input) {
        setDebugInput(payload.debug_input);
      }
    },
    onTaskCompleted: (task) => {
      if (task.taskId !== activeTaskId) return;
      const payload = readTaskPayload(task.result);
      if (payload.report) {
        setReport(payload.report);
        setDebugInput(payload.report.debug_input);
      }
      updateStreamText(payload.stream_text);
      setReportDraft((current) => mergeTaskDraft(current, payload.report_draft ?? null));
      if (payload.debug_input) {
        setDebugInput(payload.debug_input);
      }
      setTaskError(null);
      setStreamPhase('completed');
      setTaskMessage(task.message || '模型研判生成完成');
      clearActiveTask();
      void loadLatest();
    },
    onTaskFailed: (task) => {
      if (task.taskId !== activeTaskId) return;
      setTaskError(task.error || task.message || '模型研判生成失败。');
      setStreamPhase('failed');
      setTaskMessage(task.message || task.error || '模型研判生成失败');
      clearActiveTask();
    },
  });

  const displayReport = useMemo(
    () => mergeDraftIntoReport(report, reportDraft, debugInput),
    [report, reportDraft, debugInput],
  );
  const renderReport = useMemo(
    () => (hasStructuredContent(displayReport) ? displayReport : null),
    [displayReport],
  );

  const displayText = useMemo(() => {
    const candidate = isGenerating
      ? streamText
      : (displayReport?.raw_stream_output || displayReport?.full_report || reportDraft?.raw_stream_output || reportDraft?.full_report || streamText);
    return String(candidate || '').trim();
  }, [displayReport?.full_report, displayReport?.raw_stream_output, isGenerating, reportDraft?.full_report, reportDraft?.raw_stream_output, streamText]);

  const streamKind = useMemo(() => detectStreamKind(displayText), [displayText]);
  const formattedDisplayText = useMemo(() => formatStreamText(displayText), [displayText]);
  const streamStatsText = useMemo(() => {
    if (!displayText) {
      return '等待首个分片';
    }
    return `${displayText.length.toLocaleString()} 字符`;
  }, [displayText]);
  const streamContent = useMemo(() => {
    if (!formattedDisplayText) {
      return <span className="text-slate-400">模型已启动，等待首个分片返回...</span>;
    }

    if (streamKind === 'json') {
      return <pre className="market-stream-pre">{formattedDisplayText}</pre>;
    }

    return renderReportParagraphs(formattedDisplayText);
  }, [formattedDisplayText, streamKind]);

  return (
    <div className="flex min-h-[calc(100vh-2rem)] w-full flex-col gap-4 lg:h-[calc(100vh-2rem)] lg:overflow-hidden">
      <div className="flex shrink-0 flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Model Stream</p>
          <h1 className="mt-1 flex items-center gap-2 text-2xl font-semibold text-slate-950">
            <Flame className="h-6 w-6 text-amber-500" />
            市场主线
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-500">
            页面只展示模型实时输出；原始输入放在下方折叠面板里查看。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {displayReport?.model_used ? <Badge variant="info">{displayReport.model_used}</Badge> : null}
          {displayReport?._cached ? <Badge variant="default">缓存</Badge> : null}
          {isGenerating ? <Badge variant="warning">SSE 推送中</Badge> : null}
        </div>
      </div>

      <main className="min-h-0 min-w-0 flex-1 overflow-visible pr-1">
        {error ? (
          <InlineAlert
            title="加载失败"
            variant="danger"
            message={error}
            action={(
              <Button variant="outline" size="sm" onClick={() => void loadLatest()}>
                重试
              </Button>
            )}
            className="mb-4"
          />
        ) : null}

        {taskError ? (
          <InlineAlert
            title="模型研判失败"
            variant="danger"
            message={taskError}
            action={(
              <Button variant="outline" size="sm" onClick={() => void startGeneration(true)}>
                重新生成
              </Button>
            )}
            className="mb-4"
          />
        ) : null}

        {loading && !displayReport ? <Loading label="正在加载最近一次市场主线报告..." className="h-full" /> : null}

        <div className="space-y-4 pb-4">
          <Card className="border border-slate-200/80 bg-white/92" padding="lg">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div className="space-y-2">
                <p className="text-sm font-medium text-slate-900">
                  {isGenerating ? (taskMessage || `模型正在生成中${streamPhase ? `：${streamPhase}` : ''}`) : '当前展示最新模型输出'}
                </p>
                <p className="text-xs text-slate-500">
                  {displayReport?.as_of_date ? `数据日期 ${displayReport.as_of_date}` : '等待模型输出'}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <span className="flex items-center gap-2 text-xs text-slate-500">
                  <Radio className={`h-4 w-4 ${isGenerating ? 'text-cyan-600' : 'text-slate-300'}`} />
                  {isGenerating ? '实时推送中' : '空闲'}
                </span>
                {isGenerating || submitting ? (
                  <span className="inline-flex h-9 shrink-0 items-center gap-2 rounded-full border border-cyan-200 bg-cyan-50 px-4 text-sm font-medium text-cyan-700">
                    <svg
                      className="h-4 w-4 animate-spin text-cyan-500"
                      xmlns="http://www.w3.org/2000/svg"
                      fill="none"
                      viewBox="0 0 24 24"
                    >
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                      />
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                      />
                    </svg>
                    处理中
                  </span>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    className="min-w-[6.75rem] shrink-0"
                    onClick={() => void startGeneration(true)}
                  >
                    重新生成
                  </Button>
                )}
              </div>
            </div>
          </Card>

          <Card title="模型实时输出" subtitle="Streaming Output" className="border border-cyan-200 bg-cyan-50/30" padding="lg">
            <div className="mb-4 flex flex-wrap items-center gap-2 text-xs">
              <span className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 font-medium text-slate-600">
                <span className={`h-2 w-2 rounded-full ${isGenerating ? 'bg-cyan-500 shadow-[0_0_0_4px_rgba(34,211,238,0.12)]' : 'bg-slate-300'}`} />
                {renderReport ? '结构化渲染' : (streamKind === 'json' ? '原始 JSON 输出' : '原始报告输出')}
              </span>
              <span className="rounded-full border border-slate-200 bg-white px-3 py-1 font-medium text-slate-500">
                {streamStatsText}
              </span>
              {streamPhase ? (
                <span className="rounded-full border border-cyan-200 bg-cyan-50 px-3 py-1 font-medium text-cyan-700">
                  {streamPhase}
                </span>
              ) : null}
            </div>

            {renderReport ? (
              <div className="space-y-4">
                {(renderReport.overview || renderReport.market_stage?.label) ? (
                  <div className="rounded-3xl border border-cyan-200/80 bg-white/90 p-5 shadow-[0_20px_45px_-35px_rgba(8,145,178,0.45)]">
                    <div className="mb-3 flex flex-wrap items-center gap-2">
                      {renderReport.market_stage?.label ? (
                        <span className="rounded-full bg-cyan-50 px-3 py-1 text-xs font-semibold text-cyan-700">
                          {renderReport.market_stage.label}
                        </span>
                      ) : null}
                      {renderReport.as_of_date ? (
                        <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-500">
                          数据日期 {renderReport.as_of_date}
                        </span>
                      ) : null}
                    </div>
                    {renderReport.overview ? (
                      <p className="text-[1.05rem] font-semibold leading-8 text-slate-900">
                        {renderReport.overview}
                      </p>
                    ) : null}
                    {renderReport.market_stage?.description ? (
                      <p className="mt-3 text-sm leading-7 text-slate-600">
                        {renderReport.market_stage.description}
                      </p>
                    ) : null}
                  </div>
                ) : null}

                {renderReport.full_report ? (
                  <div className="rounded-3xl border border-slate-200 bg-white/88 p-5">
                    <div className="mb-3 text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
                      核心研判
                    </div>
                    {renderReportParagraphs(renderReport.full_report)}
                  </div>
                ) : null}

                {renderReport.current_mainlines && renderReport.current_mainlines.length > 0 ? (
                  <div className="space-y-3">
                    <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
                      当前主线
                    </div>
                    <div className="grid gap-3">
                      {renderReport.current_mainlines.map((item) => (
                        <div key={`${item.rank}-${item.name}`} className="rounded-3xl border border-slate-200 bg-white/88 p-5">
                          <div className="mb-3 flex flex-wrap items-center gap-2">
                            <span className="rounded-full bg-slate-900 px-3 py-1 text-xs font-semibold text-white">
                              #{item.rank}
                            </span>
                            <h4 className="text-base font-semibold text-slate-900">{item.name}</h4>
                            {item.stage ? (
                              <span className="rounded-full bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700">
                                {item.stage}
                              </span>
                            ) : null}
                          </div>
                          {item.reason ? <p className="text-sm leading-7 text-slate-700">{item.reason}</p> : null}
                          {item.focus ? (
                            <div className="mt-4 rounded-2xl bg-cyan-50/70 px-4 py-3 text-sm leading-7 text-cyan-900">
                              <span className="font-semibold">当前聚焦：</span>
                              {item.focus}
                            </div>
                          ) : null}
                          {item.branches && item.branches.length > 0 ? (
                            <div className="mt-4 flex flex-wrap gap-2">
                              {item.branches.map((branch) => (
                                <span key={branch} className="rounded-full border border-cyan-200 bg-white px-3 py-1 text-xs font-medium text-cyan-700">
                                  {branch}
                                </span>
                              ))}
                            </div>
                          ) : null}
                          {(item.risks && item.risks.length > 0) || (item.evidence && item.evidence.length > 0) ? (
                            <div className="mt-4 grid gap-3 lg:grid-cols-2">
                              {item.risks && item.risks.length > 0 ? (
                                <div className="rounded-2xl bg-rose-50/70 px-4 py-3">
                                  <div className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-rose-500">风险</div>
                                  <ul className="space-y-2 text-sm leading-6 text-slate-700">
                                    {item.risks.map((risk) => <li key={risk}>• {risk}</li>)}
                                  </ul>
                                </div>
                              ) : null}
                              {item.evidence && item.evidence.length > 0 ? (
                                <div className="rounded-2xl bg-emerald-50/70 px-4 py-3">
                                  <div className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-emerald-600">证据</div>
                                  <ul className="space-y-2 text-sm leading-6 text-slate-700">
                                    {item.evidence.map((evidence) => <li key={evidence}>• {evidence}</li>)}
                                  </ul>
                                </div>
                              ) : null}
                            </div>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}

                {renderReport.future_mainlines && renderReport.future_mainlines.length > 0 ? (
                  <div className="rounded-3xl border border-slate-200 bg-white/88 p-5">
                    <div className="mb-3 text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
                      候选主线
                    </div>
                    <div className="grid gap-3">
                      {renderReport.future_mainlines.map((item) => (
                        <div key={`${item.name}-${item.stage_hint}`} className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                          <div className="flex flex-wrap items-center gap-2">
                            <h4 className="text-sm font-semibold text-slate-900">{item.name}</h4>
                            {item.stage_hint ? (
                              <span className="rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-500">
                                {item.stage_hint}
                              </span>
                            ) : null}
                          </div>
                          {item.reason ? <p className="mt-2 text-sm leading-7 text-slate-700">{item.reason}</p> : null}
                          {item.triggers && item.triggers.length > 0 ? (
                            <div className="mt-3 flex flex-wrap gap-2">
                              {item.triggers.map((trigger) => (
                                <span key={trigger} className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs text-slate-600">
                                  {trigger}
                                </span>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}

                {renderReport.action_summary && renderReport.action_summary.length > 0 ? (
                  <div className="rounded-3xl border border-slate-200 bg-slate-950 p-5 text-white">
                    <div className="mb-3 text-xs font-semibold uppercase tracking-[0.2em] text-cyan-300">
                      行动摘要
                    </div>
                    <ul className="space-y-3">
                      {renderReport.action_summary.map((item) => (
                        <li key={item} className="rounded-2xl bg-white/8 px-4 py-3 text-sm leading-7 text-slate-100">
                          {item}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {renderReport.evidence_digest ? (
                  <div className="grid gap-3 lg:grid-cols-3">
                    {[
                      { key: 'policy', label: '政策证据', tone: 'text-cyan-700 bg-cyan-50/70' },
                      { key: 'industry', label: '产业证据', tone: 'text-violet-700 bg-violet-50/70' },
                      { key: 'market', label: '市场证据', tone: 'text-emerald-700 bg-emerald-50/70' },
                    ].map((section) => {
                      const items = renderReport.evidence_digest?.[section.key as keyof typeof renderReport.evidence_digest] || [];
                      if (!items.length) {
                        return null;
                      }
                      return (
                        <div key={section.key} className={`rounded-3xl border border-slate-200 p-5 ${section.tone}`}>
                          <div className="mb-3 text-xs font-semibold uppercase tracking-[0.18em]">
                            {section.label}
                          </div>
                          <ul className="space-y-2 text-sm leading-6">
                            {items.map((item) => <li key={item}>• {item}</li>)}
                          </ul>
                        </div>
                      );
                    })}
                  </div>
                ) : null}

                <div className="rounded-3xl border border-slate-200 bg-white/70 p-4">
                  <button
                    type="button"
                    className="flex w-full items-center justify-between text-left"
                    onClick={() => setShowRawOutput((value) => !value)}
                  >
                    <div>
                      <p className="text-sm font-semibold text-slate-900">查看原始流输出</p>
                      <p className="mt-1 text-xs text-slate-500">保留模型原始返回，便于核对结构化渲染</p>
                    </div>
                    <ChevronDown className={`h-4 w-4 text-slate-500 transition-transform ${showRawOutput ? 'rotate-180' : ''}`} />
                  </button>
                  {showRawOutput ? (
                    <div className="mt-4 market-stream-shell">
                      <div className="market-stream-rail">
                        <span className="market-stream-rail-label">RAW</span>
                        <span className="market-stream-rail-label">SOURCE</span>
                      </div>
                      <div
                        className={`market-stream-panel ${streamKind === 'json' ? 'market-stream-json' : 'market-stream-report'}`}
                      >
                        {streamContent}
                      </div>
                    </div>
                  ) : null}
                </div>
              </div>
            ) : (
              <div className="market-stream-shell">
                <div className="market-stream-rail">
                  <span className="market-stream-rail-label">LIVE</span>
                  <span className="market-stream-rail-label">OUTPUT</span>
                </div>
                <div
                  className={`market-stream-panel ${streamKind === 'json' ? 'market-stream-json' : 'market-stream-report'}`}
                >
                  {streamContent}
                </div>
              </div>
            )}
          </Card>

          <Card className="border border-slate-200/80 bg-white/92" padding="lg">
            <button
              type="button"
              className="flex w-full items-center justify-between text-left"
              onClick={() => setShowRawInput((value) => !value)}
            >
              <div>
                <p className="text-sm font-semibold text-slate-900">查看模型原始输入</p>
                <p className="mt-1 text-xs text-slate-500">包括 system prompt、user prompt 和 evidence pack</p>
              </div>
              <ChevronDown className={`h-4 w-4 text-slate-500 transition-transform ${showRawInput ? 'rotate-180' : ''}`} />
            </button>

            {showRawInput ? (
              <div className="mt-4 space-y-4">
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-[0.16em] text-slate-500">System Prompt</p>
                  <pre className="overflow-x-auto rounded-2xl border border-slate-200 bg-slate-50 p-4 text-xs leading-6 text-slate-700 whitespace-pre-wrap">
                    {displayReport?.debug_input?.system_prompt || '当前还没有 system prompt。'}
                  </pre>
                </div>
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-[0.16em] text-slate-500">User Prompt</p>
                  <pre className="overflow-x-auto rounded-2xl border border-slate-200 bg-slate-50 p-4 text-xs leading-6 text-slate-700 whitespace-pre-wrap">
                    {displayReport?.debug_input?.user_prompt || '当前还没有 user prompt。'}
                  </pre>
                </div>
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-[0.16em] text-slate-500">Evidence Pack</p>
                  <pre className="overflow-x-auto rounded-2xl border border-slate-200 bg-slate-50 p-4 text-xs leading-6 text-slate-700 whitespace-pre-wrap">
                    {displayReport?.debug_input?.evidence_pack
                      ? prettyJson(displayReport.debug_input.evidence_pack)
                      : '当前还没有 evidence pack。'}
                  </pre>
                </div>
              </div>
            ) : null}
          </Card>
        </div>
      </main>
    </div>
  );
};

export default MarketLeadersPage;
