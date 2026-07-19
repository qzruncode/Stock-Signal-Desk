import type { FC } from 'react';

import { ThreadPrimitive } from '@assistant-ui/react';

import { cn } from '../../utils/cn';
import { ASSISTANT_QUICK_ACTIONS } from '../../utils/assistantQuickActions';

const AssistantQuickActions: FC = () => (
  <section
    className="mt-4 w-full rounded-2xl border border-border/70 bg-card/55 p-3 text-left shadow-sm"
    aria-labelledby="assistant-quick-actions-title"
    data-testid="assistant-quick-actions"
  >
    <div className="flex flex-wrap items-center justify-between gap-2 px-1">
      <p id="assistant-quick-actions-title" className="text-xs font-medium text-muted-foreground">
        更多可直接执行的任务
      </p>
      <span className="text-[10px] text-muted-foreground/75">点击即发送</span>
    </div>
    <div className="mt-2 flex flex-wrap gap-1.5">
      {ASSISTANT_QUICK_ACTIONS.map((item) => (
        <ThreadPrimitive.Suggestion
          key={item.label}
          className={cn(
            'rounded-full border border-border/75 bg-background/60 px-3 py-1.5 text-xs text-foreground/80 transition',
            'hover:border-primary/30 hover:bg-primary/5 hover:text-primary',
          )}
          prompt={item.prompt}
          send
          method="replace"
          title={`发送：${item.prompt}`}
        >
          {item.label}
        </ThreadPrimitive.Suggestion>
      ))}
    </div>
  </section>
);

export default AssistantQuickActions;
