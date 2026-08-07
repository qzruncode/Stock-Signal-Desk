import type { FC } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useThread } from '@assistant-ui/react';
import { ListTreeIcon } from 'lucide-react';
import {
  getChatQuestionDomId,
  locateChatQuestion,
} from '../../utils/chatQuestionLocator';
import { cn } from '../../utils/cn';

type QuestionItem = {
  id: string;
  label: string;
};

export const QuestionNavigator: FC<{ className?: string }> = ({ className }) => {
  const messages = useThread((state) => state.messages);
  const questions = useMemo<QuestionItem[]>(
    () => messages.flatMap((message) => {
      if (message.role !== 'user') return [];
      const label = message.content
        .map((part) => (part.type === 'text' ? part.text : ''))
        .join(' ')
        .replace(/\s+/g, ' ')
        .trim();
      return [{ id: message.id, label: label || '附件问题' }];
    }),
    [messages],
  );
  const [isOpen, setIsOpen] = useState(false);
  const [activeQuestionId, setActiveQuestionId] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  const currentQuestionId = questions.some((question) => question.id === activeQuestionId)
    ? activeQuestionId
    : questions.at(-1)?.id ?? null;

  useEffect(() => {
    if (!isOpen) return;

    const closeOnOutsidePress = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsOpen(false);
    };
    document.addEventListener('pointerdown', closeOnOutsidePress);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePress);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [isOpen]);

  useEffect(() => {
    const viewport = document.querySelector<HTMLElement>('[data-chat-thread-viewport="true"]');
    const ownerWindow = viewport?.ownerDocument.defaultView;
    if (!viewport || !ownerWindow || questions.length === 0) return;

    let animationFrame = 0;
    const updateActiveQuestion = () => {
      const activationLine = viewport.getBoundingClientRect().top + 72;
      let nextQuestionId = questions[0]?.id ?? null;
      for (const question of questions) {
        const element = document.getElementById(getChatQuestionDomId(question.id));
        if (!element || element.getBoundingClientRect().top > activationLine) break;
        nextQuestionId = question.id;
      }
      setActiveQuestionId((current) => (
        current === nextQuestionId ? current : nextQuestionId
      ));
    };
    const scheduleUpdate = () => {
      ownerWindow.cancelAnimationFrame(animationFrame);
      animationFrame = ownerWindow.requestAnimationFrame(updateActiveQuestion);
    };

    scheduleUpdate();
    viewport.addEventListener('scroll', scheduleUpdate, { passive: true });
    ownerWindow.addEventListener('resize', scheduleUpdate);
    return () => {
      ownerWindow.cancelAnimationFrame(animationFrame);
      viewport.removeEventListener('scroll', scheduleUpdate);
      ownerWindow.removeEventListener('resize', scheduleUpdate);
    };
  }, [questions]);

  if (questions.length === 0) return null;

  return (
    <div
      ref={rootRef}
      className={cn('flex h-8 shrink-0 justify-end pointer-events-none sm:h-9', className)}
    >
      <div className="relative pointer-events-auto">
        <button
          type="button"
          className={cn(
            'relative flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border',
            'bg-card text-muted-foreground shadow-sm transition hover:text-foreground',
            'sm:h-9 sm:w-9',
            isOpen && 'border-primary/30 text-foreground shadow-md',
          )}
          aria-label="打开问题导航"
          aria-expanded={isOpen}
          aria-controls="chat-question-navigation"
          title="问题导航"
          onClick={() => setIsOpen((value) => !value)}
        >
          <ListTreeIcon className="size-3.5 text-primary" />
          <span className="absolute right-1 top-1 min-w-3.5 rounded-full border border-border/70 bg-card px-0.5 text-center text-[9px] leading-3.5 tabular-nums text-muted-foreground">
            {questions.length}
          </span>
        </button>

        {isOpen ? (
          <div
            id="chat-question-navigation"
            className="absolute right-0 top-9 w-[min(20rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-border bg-card shadow-xl sm:top-10"
          >
            <div className="flex items-center justify-between border-b border-border/70 px-3 py-2">
              <span className="text-xs font-semibold text-foreground">本次会话的问题</span>
              <span className="text-[10px] text-muted-foreground">点击快速定位</span>
            </div>
            <ol className="max-h-[min(60vh,28rem)] space-y-0.5 overflow-y-auto p-1.5">
              {questions.map((question, index) => {
                const isActive = question.id === currentQuestionId;
                return (
                  <li key={question.id}>
                    <button
                      type="button"
                      className={cn(
                        'flex w-full items-start gap-2 rounded-lg px-2 py-2 text-left transition',
                        isActive
                          ? 'bg-primary/[0.08] text-foreground'
                          : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                      )}
                      aria-label={`第 ${index + 1} 个问题：${question.label}`}
                      aria-current={isActive ? 'true' : undefined}
                      title={question.label}
                      onClick={() => {
                        locateChatQuestion(question.id);
                        setActiveQuestionId(question.id);
                        setIsOpen(false);
                      }}
                    >
                      <span
                        className={cn(
                          'mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold tabular-nums',
                          isActive ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground',
                        )}
                      >
                        {index + 1}
                      </span>
                      <span className="line-clamp-2 text-xs leading-5">{question.label}</span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </div>
        ) : null}
      </div>
    </div>
  );
};
