import type { FC } from 'react';

/**
 * Shared live execution cue for every agent product mode.
 *
 * Keep this separate from any mode-specific process panel: the dots answer the
 * message-level question "is the assistant still working?", while the Plan
 * and Team panels explain what it is doing.
 */
export const AssistantTypingIndicator: FC = () => (
  <div
    className="flex min-h-7 items-center justify-start py-1"
    role="status"
    aria-label="主 Agent 回复生成中"
    aria-live="polite"
    data-assistant-typing-indicator
  >
    <span
      className="inline-flex items-center gap-1 pl-1"
      aria-hidden="true"
      data-assistant-typing-dots
    >
      <span
        className="size-1.5 animate-bounce rounded-full bg-primary/65 motion-reduce:animate-none"
        data-assistant-typing-dot
        style={{ animationDelay: '-240ms' }}
      />
      <span
        className="size-1.5 animate-bounce rounded-full bg-primary/65 motion-reduce:animate-none"
        data-assistant-typing-dot
        style={{ animationDelay: '-120ms' }}
      />
      <span
        className="size-1.5 animate-bounce rounded-full bg-primary/65 motion-reduce:animate-none"
        data-assistant-typing-dot
      />
    </span>
    <span className="sr-only">主 Agent 正在生成模型回复</span>
  </div>
);
