import type { FC } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { TextMessagePartProps } from '@assistant-ui/react';
import { splitAssistantText } from '../../utils/assistantTextSplit';

export const AssistantMarkdown: FC<{ text: string }> = ({ text }) => {
  const { content, stopped } = splitAssistantText(text);

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
              a: ({ href, children }) => (
                <a
                  href={href}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="font-medium text-primary underline decoration-primary/30 underline-offset-4 transition hover:decoration-primary"
                >
                  {children}
                </a>
              ),
            }}
          >
            {content}
          </Markdown>
        </div>
      )}
    </div>
  );
};

/** Adapter kept for assistant-ui part registries outside the chat timeline. */
export const AssistantMarkdownText: FC<TextMessagePartProps> = ({ text }) => (
  <AssistantMarkdown text={text} />
);
