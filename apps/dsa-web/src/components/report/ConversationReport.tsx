import React, { useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { AnalysisResult, AnalysisReport } from '../../types/analysis';
import { Tooltip } from '../common/Tooltip';

interface ConversationReportProps {
  data: AnalysisResult | AnalysisReport;
  isHistory?: boolean;
}

type ConversationLike = {
  prompt?: unknown;
  response?: unknown;
  modelUsed?: unknown;
  model_used?: unknown;
  templateName?: unknown;
  template_name?: unknown;
};

const asText = (value: unknown): string => (typeof value === 'string' ? value.trim() : '');

const extractConversation = (report: AnalysisReport): ConversationLike | undefined => {
  if (report.conversation && typeof report.conversation === 'object') {
    return report.conversation as ConversationLike;
  }

  const rawResult = report.details?.rawResult;
  if (!rawResult || typeof rawResult !== 'object') {
    return undefined;
  }

  const rawConversation = (rawResult as { conversation?: unknown }).conversation;
  return rawConversation && typeof rawConversation === 'object'
    ? rawConversation as ConversationLike
    : undefined;
};

export const ConversationReport: React.FC<ConversationReportProps> = ({
  data,
}) => {
  const report: AnalysisReport = 'report' in data ? data.report : data;
  const { meta } = report;
  const conversation = extractConversation(report);

  const [promptExpanded, setPromptExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const modelUsed = asText(meta.modelUsed) || asText(conversation?.modelUsed) || asText(conversation?.model_used);
  const templateName = asText(conversation?.templateName) || asText(conversation?.template_name);
  const promptContent = asText(conversation?.prompt);
  const responseContent =
    asText(conversation?.response)
    || asText(report.details?.newsContent)
    || asText(report.summary?.analysisSummary);
  const responseDisplay = responseContent || 'AI 正在生成输出，稍后会在这里展示完整回吐数据。';
  const hasOutput = !!(promptContent || responseContent);

  const shouldShowModel = Boolean(
    modelUsed && !['unknown', 'error', 'none', 'null', 'n/a'].includes(modelUsed.toLowerCase()),
  );

  const handleCopyPrompt = () => {
    if (!promptContent) return;
    void navigator.clipboard.writeText(promptContent).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleCopyResponse = () => {
    if (!responseContent) return;
    void navigator.clipboard.writeText(responseContent).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  if (!hasOutput) {
    return (
      <div className="rounded-xl border border-subtle bg-surface/70 px-4 py-8 text-center text-sm text-muted-text">
        {'这条历史记录暂未保存 AI 输出，可打开完整报告查看结构化结果。'}
      </div>
    );
  }

  return (
    <div className="space-y-4 animate-fade-in">
      {/* Model & template info bar */}
      {(shouldShowModel || templateName) && (
        <div className="flex flex-wrap items-center gap-3 px-1 text-xs text-muted-text">
          {templateName && (
            <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2.5 py-1 text-primary">
              <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              {templateName}
            </span>
          )}
          {shouldShowModel && (
            <span className="inline-flex items-center gap-1">
              <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
              {modelUsed}
            </span>
          )}
        </div>
      )}

      {promptContent ? (
        <div className="rounded-xl border border-subtle bg-surface/50 overflow-hidden">
          <button
            type="button"
            onClick={() => setPromptExpanded(!promptExpanded)}
            className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left text-sm font-medium text-foreground hover:bg-hover/50 transition-colors"
          >
            <span className="flex items-center gap-2">
              <svg className="h-4 w-4 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
              </svg>
              注入提示词
            </span>
            <span className="flex items-center gap-2">
              <Tooltip content="复制提示词">
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(e) => { e.stopPropagation(); handleCopyPrompt(); }}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.stopPropagation(); handleCopyPrompt(); } }}
                  className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-text hover:bg-surface hover:text-foreground transition-colors"
                >
                  {copied ? (
                    <svg className="h-3.5 w-3.5 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  ) : (
                    <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    </svg>
                  )}
                </span>
              </Tooltip>
              <svg
                className={`h-4 w-4 text-muted-text transition-transform ${promptExpanded ? 'rotate-180' : ''}`}
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </span>
          </button>
          {promptExpanded && (
            <div className="border-t border-subtle px-4 py-3">
              <pre className="overflow-x-auto whitespace-pre-wrap break-words text-xs text-secondary-text leading-relaxed max-h-96 overflow-y-auto">
                {promptContent}
              </pre>
            </div>
          )}
        </div>
      ) : null}

      {/* Response card */}
      <div className="rounded-xl border border-subtle bg-surface/70 overflow-hidden">
        <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-subtle">
          <span className="flex items-center gap-2 text-sm font-medium text-foreground">
            <svg className="h-4 w-4 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
            AI 分析输出
          </span>
          <Tooltip content="复制回复">
            <button
              type="button"
              onClick={handleCopyResponse}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-text hover:bg-surface hover:text-foreground transition-colors"
            >
              {copied ? (
                <svg className="h-3.5 w-3.5 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              ) : (
                <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
              )}
            </button>
          </Tooltip>
        </div>
        <div className="px-4 py-4">
          <div
            className="home-markdown-prose prose prose-invert prose-sm max-w-none
              prose-headings:text-foreground prose-headings:font-semibold prose-headings:mt-4 prose-headings:mb-2
              prose-h1:text-xl
              prose-h2:text-lg
              prose-h3:text-base
              prose-p:leading-relaxed prose-p:mb-3 prose-p:last:mb-0
              prose-strong:text-foreground prose-strong:font-semibold
              prose-ul:my-2 prose-ol:my-2 prose-li:my-1
              prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none
              prose-pre:border
              prose-table:border-collapse
              prose-hr:my-4
              prose-a:no-underline hover:prose-a:underline
              prose-blockquote:text-secondary-text
              whitespace-pre-line break-words
            "
          >
            <Markdown remarkPlugins={[remarkGfm]}>
              {responseDisplay}
            </Markdown>
          </div>
        </div>
      </div>
    </div>
  );
};
