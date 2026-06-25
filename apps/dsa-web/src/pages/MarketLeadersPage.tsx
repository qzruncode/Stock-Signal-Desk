import React, { useMemo, useState } from 'react';
import { ChevronDown, Flame, Radio } from 'lucide-react';
import { Badge, Button, InlineAlert, Loading } from '../components/common';
import { useMarketMainlineTask } from '../hooks/useMarketMainlineTask';
import { prettyJson } from '../utils/marketMainlineFormat';

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

const MarketLeadersPage: React.FC = () => {
  const {
    loading,
    submitting,
    error,
    taskError,
    streamPhase,
    taskMessage,
    isGenerating,
    displayReport,
    renderReport,
    formattedDisplayText,
    streamStatsText,
    streamKind,
    debugInput,
    loadLatest,
    startGeneration,
  } = useMarketMainlineTask();

  const [showRawInput, setShowRawInput] = useState(false);
  const [showRawOutput, setShowRawOutput] = useState(false);

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
    <div className="market-mainline-page flex min-h-[calc(100vh-2rem)] w-full flex-col gap-4">
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

      <main className="min-w-0 flex-1">
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
          <section className="market-mainline-surface market-mainline-status">
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
          </section>

          <section className="market-mainline-surface space-y-4">
            <div>
              <span className="label-uppercase">Streaming Output</span>
              <h2 className="mt-1 text-2xl font-semibold text-slate-950">模型实时输出</h2>
            </div>
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
                  <div className="market-mainline-featured">
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
                  <div className="market-mainline-block">
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
                        <div key={`${item.rank}-${item.name}`} className="market-mainline-block">
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
                  <div className="market-mainline-block">
                    <div className="mb-3 text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
                      候选主线
                    </div>
                    <div className="grid gap-3">
                      {renderReport.future_mainlines.map((item) => (
                        <div key={`${item.name}-${item.stage_hint}`} className="market-mainline-muted-block">
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
                  <div className="market-mainline-dark-block">
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
                        <div key={section.key} className={`market-mainline-muted-block ${section.tone}`}>
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

                <div className="market-mainline-muted-block">
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
                    <div className="mt-4">
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
              <div
                className={`market-stream-panel ${streamKind === 'json' ? 'market-stream-json' : 'market-stream-report'}`}
              >
                <div
                  className="min-h-[12rem]"
                >
                  {streamContent}
                </div>
              </div>
            )}
          </section>

          <section className="market-mainline-surface">
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
                  <pre className="overflow-x-auto rounded-[1.25rem] bg-slate-50 p-4 text-xs leading-6 text-slate-700 whitespace-pre-wrap">
                    {debugInput?.system_prompt || '当前还没有 system prompt。'}
                  </pre>
                </div>
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-[0.16em] text-slate-500">User Prompt</p>
                  <pre className="overflow-x-auto rounded-[1.25rem] bg-slate-50 p-4 text-xs leading-6 text-slate-700 whitespace-pre-wrap">
                    {debugInput?.user_prompt || '当前还没有 user prompt。'}
                  </pre>
                </div>
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-[0.16em] text-slate-500">Evidence Pack</p>
                  <pre className="overflow-x-auto rounded-[1.25rem] bg-slate-50 p-4 text-xs leading-6 text-slate-700 whitespace-pre-wrap">
                    {debugInput?.evidence_pack
                      ? prettyJson(debugInput.evidence_pack)
                      : '当前还没有 evidence pack。'}
                  </pre>
                </div>
              </div>
            ) : null}
          </section>
        </div>
      </main>
    </div>
  );
};

export default MarketLeadersPage;
