import { useEffect, useMemo, useRef, useState, type FC } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { TextMessagePartProps } from '@assistant-ui/react';
import { Tooltip } from '../common/Tooltip';
import {
  assistantEvidenceIdFromHref,
  assistantEvidenceIndexFromTrace,
  assistantEvidenceReferenceForId,
  replaceAssistantEvidenceMarkers,
  type AssistantEvidenceReference,
} from '../../utils/assistantEvidence';
import { splitAssistantText } from '../../utils/assistantTextSplit';

type AssistantMarkdownProps = {
  text: string;
  evidence?: unknown;
  /**
   * Reveal live assistant text progressively. Persisted/terminal messages
   * leave this disabled so a conversation does not replay on hydration.
   */
  animate?: boolean;
};

const TEXT_REVEAL_MIN_DURATION_MS = 240;
const TEXT_REVEAL_MAX_DURATION_MS = 1_600;
const TEXT_REVEAL_MS_PER_CHARACTER = 22;

const textRevealDurationMs = (text: string): number => Math.min(
  TEXT_REVEAL_MAX_DURATION_MS,
  Math.max(TEXT_REVEAL_MIN_DURATION_MS, text.length * TEXT_REVEAL_MS_PER_CHARACTER),
);

/**
 * The runtime may deliver one natural-language progress sentence as a single
 * text part. Keep the transport event intact, but make that part feel like a
 * live assistant response in the chat. This is deliberately a UI-only
 * reveal: it never invents text or changes the persisted message.
 */
const useProgressiveText = (text: string, animate: boolean): string => {
  const shouldAnimate = animate
    && typeof window !== 'undefined'
    && !(typeof window !== 'undefined'
      && typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
    && typeof window.requestAnimationFrame === 'function';
  const initialText = shouldAnimate ? '' : text;
  const [visibleText, setVisibleText] = useState(initialText);
  const visibleTextRef = useRef(initialText);

  useEffect(() => {
    if (!shouldAnimate || !text) {
      visibleTextRef.current = text;
      return undefined;
    }

    const currentText = visibleTextRef.current;
    const startLength = text.startsWith(currentText) ? currentText.length : 0;
    const remainingLength = text.length - startLength;
    if (remainingLength <= 0) return undefined;

    const startedAt = window.performance?.now() ?? Date.now();
    const duration = textRevealDurationMs(text.slice(startLength));
    let frameId = 0;

    const reveal = () => {
      // Use the same clock for both timestamps. Some browser/test
      // implementations expose an animation-frame timestamp with a
      // different origin than performance.now().
      const now = window.performance?.now() ?? Date.now();
      const elapsed = now - startedAt;
      const progress = Math.min(1, Math.max(0, elapsed / duration));
      const nextLength = progress >= 1
        ? text.length
        : startLength + Math.max(1, Math.ceil(remainingLength * progress));
      const nextText = text.slice(0, nextLength);

      if (nextText !== visibleTextRef.current) {
        visibleTextRef.current = nextText;
        setVisibleText(nextText);
      }
      if (nextLength < text.length) {
        frameId = window.requestAnimationFrame(reveal);
      }
    };

    frameId = window.requestAnimationFrame(reveal);
    return () => window.cancelAnimationFrame(frameId);
  }, [shouldAnimate, text]);

  return shouldAnimate ? visibleText : text;
};

const evidenceSourceLabel = (value: string): string => {
  const trimmed = value.trim();
  if (!trimmed) return '';
  try {
    return new URL(trimmed).hostname.replace(/^www\./i, '');
  } catch {
    return trimmed.replace(/^tool:/i, '');
  }
};

const EVIDENCE_ATTRIBUTE_LABELS: Record<string, string> = {
  amount: '成交额',
  amount_unit: '成交额单位',
  amplitude: '振幅',
  book_value_per_share: '每股净资产',
  change: '涨跌额',
  change_pct: '涨跌幅',
  circulating_market_value: '流通市值',
  close: '收盘价',
  current_ratio: '流动比率',
  deducted_net_profit_yoy: '扣非净利润同比',
  high: '最高',
  ipo_date: '上市日期',
  low: '最低',
  market: '市场',
  market_value_unit: '市值单位',
  open: '今开',
  parent_net_profit: '归母净利润',
  pe_ttm: '市盈率（TTM）',
  pb: '市净率',
  previous_close: '昨收',
  price: '最新价',
  report_period: '报告期',
  revenue_latest: '最新营收',
  sector: '行业',
  status: '状态',
  total_market_value: '总市值',
  trade_time: '成交时间',
  turnover_rate: '换手率',
  volume: '成交量',
  volume_unit: '成交量单位',
};

const evidenceAttributeLabel = (name: string): string => (
  EVIDENCE_ATTRIBUTE_LABELS[name] ?? name.replaceAll('_', ' ')
);

const EvidenceTooltipContent: FC<{
  label: string;
  reference: AssistantEvidenceReference;
}> = ({ label, reference }) => {
  const sources = [...reference.sourceLabels, ...reference.sourceRefs]
    .map(evidenceSourceLabel)
    .filter(Boolean)
    .filter((source, index, values) => values.indexOf(source) === index)
    .slice(0, 4);
  const visibleItems = reference.resultItems.slice(0, 3);
  const hasReferenceContent = Boolean(
    reference.resultSummary || reference.resultItems.length > 0,
  );
  const hasMetadata = Boolean(
    reference.toolName
      || sources.length > 0
      || reference.dataTime
      || reference.observedAt
      || reference.partial
      || reference.warnings.length > 0
      || reference.errors.length > 0,
  );
  const hasDetails = Boolean(
    reference.resultSummary
      || reference.resultItems.length > 0
      || sources.length > 0
      || reference.dataTime
      || reference.observedAt
      || reference.warnings.length > 0
      || reference.errors.length > 0,
  );

  return (
    <span className="block max-w-[24rem] whitespace-normal">
      <span className="block text-[11px] font-medium tracking-wide text-muted-foreground">
        参考内容 · {label}
      </span>
      {reference.resultSummary ? (
        <span className="mt-1 block break-words font-medium leading-5 text-foreground">
          {reference.resultSummary}
        </span>
      ) : null}
      {visibleItems.length > 0 ? (
        <span className="mt-1.5 block space-y-1">
          {visibleItems.map((item, index) => (
            <span key={`${item.title}-${item.url ?? index}`} className="block break-words">
              <span className="font-medium leading-5 text-foreground">{item.title}</span>
              {item.summary ? (
                <span className="mt-0.5 block text-foreground/90">{item.summary}</span>
              ) : null}
              {item.attributes?.length ? (
                <span className="mt-0.5 block text-foreground/80">
                  {item.attributes
                    .map((attribute) => `${evidenceAttributeLabel(attribute.name)}：${attribute.value}`)
                    .join(' · ')}
                </span>
              ) : null}
              {item.source || item.publishedAt ? (
                <span className="mt-0.5 block text-[10px] leading-4 text-muted-foreground">
                  {[item.source, item.publishedAt].filter(Boolean).join(' · ')}
                </span>
              ) : null}
            </span>
          ))}
        </span>
      ) : null}
      {reference.resultItems.length > visibleItems.length ? (
        <span className="mt-1 block text-[10px] text-muted-foreground">
          另有 {reference.resultItems.length - visibleItems.length} 条参考记录
        </span>
      ) : null}
      {!hasReferenceContent ? (
        <span className="mt-1 block text-foreground/80">
          {hasMetadata ? '该证据暂无可展示的结构化摘录。' : '证据详情暂不可用。'}
        </span>
      ) : null}
      {hasMetadata ? (
        <span
          data-evidence-metadata
          className="mt-2 block border-t border-border/60 pt-1 text-[10px] leading-4 text-muted-foreground"
        >
          {reference.toolName ? <span className="block">工具：{reference.toolName}</span> : null}
          {sources.length > 0 ? (
            <span className="block break-words">来源：{sources.join('、')}</span>
          ) : null}
          {reference.dataTime ? (
            <span className="block">数据时间：{reference.dataTime}</span>
          ) : reference.observedAt ? (
            <span className="block">获取时间：{reference.observedAt}</span>
          ) : null}
          {reference.partial ? <span className="block text-amber-700">提示：本次仅返回部分结果</span> : null}
          {reference.warnings.map((warning) => (
            <span key={`warning-${warning}`} className="block text-amber-700">提示：{warning}</span>
          ))}
          {reference.errors.map((error) => (
            <span key={`error-${error}`} className="block text-red-700">异常：{error}</span>
          ))}
        </span>
      ) : null}
      {!hasDetails ? (
        <span className="block break-all font-mono text-[10px] text-muted-foreground/80">
          证据 ID：{reference.evidenceId}
        </span>
      ) : null}
    </span>
  );
};

const EvidenceCitation: FC<{
  evidenceId: string;
  label: string;
  reference?: AssistantEvidenceReference;
}> = ({ evidenceId, label, reference }) => {
  const resolvedReference = reference ?? {
    evidenceId,
    sourceLabels: [],
    sourceRefs: [],
    resultItems: [],
    warnings: [],
    errors: [],
  };
  return (
    <Tooltip
      focusable
      interactive
      ariaLabel={`查看证据 ${label}`}
      content={<EvidenceTooltipContent label={label} reference={resolvedReference} />}
      className="mx-0.5 align-baseline cursor-help"
      contentClassName="max-w-[24rem] whitespace-normal"
    >
      <span className="align-[0.08em] text-[0.85em] font-semibold leading-none text-primary">
        {label}
      </span>
    </Tooltip>
  );
};

export const AssistantMarkdown: FC<AssistantMarkdownProps> = ({ text, evidence, animate = false }) => {
  const renderedText = useProgressiveText(text, animate);
  const { content, stopped } = splitAssistantText(renderedText);
  const evidenceIndex = useMemo(() => assistantEvidenceIndexFromTrace(evidence), [evidence]);
  const renderedContent = useMemo(
    () => replaceAssistantEvidenceMarkers(content, evidenceIndex),
    [content, evidenceIndex],
  );

  return (
    <div className="w-full min-w-0 space-y-2">
      {stopped && !content.trim() && (
        <p className="text-sm text-muted-foreground">本次生成已停止，未产生最终回答。</p>
      )}

      {content.trim() && (
        <div className="assistant-markdown w-full min-w-0">
          <Markdown
            remarkPlugins={[remarkGfm]}
            components={{
              h1: ({ children }) => <h1 className="mb-2 mt-0.5 text-lg font-semibold text-foreground">{children}</h1>,
              h2: ({ children }) => <h2 className="mb-1.5 mt-3 text-base font-semibold text-foreground first:mt-0">{children}</h2>,
              h3: ({ children }) => <h3 className="mb-1.5 mt-2.5 text-sm font-semibold text-foreground first:mt-0">{children}</h3>,
              p: ({ children }) => <p className="my-1.5 leading-6 text-foreground/90">{children}</p>,
              ul: ({ children }) => <ul className="my-1.5 list-disc space-y-0.5 pl-5">{children}</ul>,
              ol: ({ children }) => <ol className="my-1.5 list-decimal space-y-0.5 pl-5">{children}</ol>,
              li: ({ children }) => <li className="leading-6">{children}</li>,
              blockquote: ({ children }) => (
                <blockquote className="my-2 border-l-2 border-primary/25 bg-primary/[0.035] py-1.5 pl-3 text-muted-foreground">
                  {children}
                </blockquote>
              ),
              table: ({ children }) => (
                <div className="my-2 overflow-x-auto rounded-md border border-border bg-card/60">
                  <table className="w-max min-w-full border-collapse text-left text-[11px] sm:text-xs">
                    {children}
                  </table>
                </div>
              ),
              tr: ({ children }) => (
                <tr className="[&:last-child_td]:border-b-0">
                  {children}
                </tr>
              ),
              th: ({ children }) => (
                <th className="border-b border-border bg-muted px-3 py-2 font-semibold whitespace-nowrap text-foreground first:min-w-20 first:w-20 sm:px-3.5">
                  {children}
                </th>
              ),
              td: ({ children }) => (
                <td className="border-b border-border px-3 py-2 align-top leading-6 break-words first:min-w-20 first:w-20 first:whitespace-nowrap last:min-w-[16rem] sm:px-3.5 sm:last:min-w-[20rem]">
                  {children}
                </td>
              ),
              code: ({ children }) => (
                <code className="rounded-md border border-border bg-muted/70 px-1.5 py-0.5 font-mono text-[0.9em] text-foreground/85">
                  {children}
                </code>
              ),
              pre: ({ children }) => (
                <div className="my-3 overflow-hidden rounded-xl border border-border bg-muted/35">
                  <div className="flex items-center justify-between border-b border-border px-3 py-2">
                    <span className="text-xs font-medium text-muted-foreground">数据摘录</span>
                  </div>
                  <pre className="max-h-80 overflow-auto p-3 font-mono text-xs leading-6 text-foreground/85 [tab-size:2]">
                    {children}
                  </pre>
                </div>
              ),
              strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
              a: ({ href, children }) => {
                const evidenceId = assistantEvidenceIdFromHref(href);
                if (evidenceId) {
                  return (
                    <EvidenceCitation
                      evidenceId={evidenceId}
                      label={String(children)}
                      reference={assistantEvidenceReferenceForId(evidenceIndex, evidenceId)}
                    />
                  );
                }
                return (
                  <a
                    href={href}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="font-medium text-primary underline decoration-primary/30 underline-offset-4 transition hover:decoration-primary"
                  >
                    {children}
                  </a>
                );
              },
            }}
          >
            {renderedContent}
          </Markdown>
        </div>
      )}
    </div>
  );
};

/** Adapter kept for assistant-ui part registries outside the chat timeline. */
export const AssistantMarkdownText: FC<TextMessagePartProps & { animate?: boolean }> = ({ text, animate = false }) => (
  <AssistantMarkdown text={text} animate={animate} />
);
