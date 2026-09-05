import { HoverCard as HoverCardPrimitive, Tooltip as TooltipPrimitive } from 'radix-ui';
import type React from 'react';
import { useEffect, useId, useSyncExternalStore } from 'react';
import { cn } from '../../utils/cn';

interface TooltipProps {
  content: React.ReactNode;
  children: React.ReactNode;
  side?: 'top' | 'bottom';
  focusable?: boolean;
  /** Use an interactive hover card when the content can be hovered, selected, or clicked. */
  interactive?: boolean;
  ariaLabel?: string;
  className?: string;
  contentClassName?: string;
}

const tooltipContentClassName =
  'pointer-events-auto z-[120] min-w-max max-w-[18rem] rounded-xl border border-border/70 bg-elevated/95 px-3 py-1.5 text-xs leading-5 text-foreground shadow-[0_16px_40px_rgba(3,8,20,0.18)] backdrop-blur-xl outline-none data-[state=open]:animate-tooltip-in data-[state=closed]:animate-tooltip-out motion-reduce:animate-none';

const interactiveTooltipStore = (() => {
  let activeId: string | null = null;
  const listeners = new Set<() => void>();

  const notify = () => {
    listeners.forEach((listener) => listener());
  };

  return {
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getSnapshot: () => activeId,
    getServerSnapshot: () => null,
    claim: (id: string) => {
      if (activeId === id) return;
      activeId = id;
      notify();
    },
    release: (id: string) => {
      if (activeId !== id) return;
      activeId = null;
      notify();
    },
  };
})();

/**
 * Shared hover surface. Radix owns the trigger/content handoff and pointer
 * grace area so a user can move into the floating content without racing a
 * local close timer. Interactive instances also share one active surface so
 * switching triggers cannot leave two detail layers mounted together. The
 * active instance is the only one that mounts its content, so a closing
 * animation can never overlap the next detail layer.
 */
export const Tooltip: React.FC<TooltipProps> = ({
  content,
  children,
  side = 'top',
  focusable = false,
  interactive = false,
  ariaLabel,
  className = '',
  contentClassName = '',
}) => {
  const tooltipId = useId();
  const interactiveTooltipId = useId();
  const activeInteractiveTooltipId = useSyncExternalStore(
    interactiveTooltipStore.subscribe,
    interactiveTooltipStore.getSnapshot,
    interactiveTooltipStore.getServerSnapshot,
  );
  const isInteractiveTooltipActive = activeInteractiveTooltipId === interactiveTooltipId;

  useEffect(() => {
    if (!interactive) return undefined;
    return () => interactiveTooltipStore.release(interactiveTooltipId);
  }, [interactive, interactiveTooltipId]);

  if (!content) {
    return <>{children}</>;
  }

  const trigger = (
    <span
      className={cn('inline-flex', className)}
      tabIndex={focusable ? 0 : undefined}
      aria-label={ariaLabel}
    >
      {children}
    </span>
  );

  if (interactive) {
    return (
      <HoverCardPrimitive.Root
        open={activeInteractiveTooltipId === interactiveTooltipId}
        onOpenChange={(open) => {
          if (open) {
            interactiveTooltipStore.claim(interactiveTooltipId);
          } else {
            interactiveTooltipStore.release(interactiveTooltipId);
          }
        }}
        openDelay={0}
        closeDelay={500}
      >
        <HoverCardPrimitive.Trigger asChild>{trigger}</HoverCardPrimitive.Trigger>
        <HoverCardPrimitive.Portal>
          {isInteractiveTooltipActive ? (
            <HoverCardPrimitive.Content
              id={tooltipId}
              role="tooltip"
              aria-label={ariaLabel}
              side={side}
              sideOffset={8}
              align="center"
              collisionPadding={8}
              className={cn(
                tooltipContentClassName,
                side === 'top' ? 'origin-bottom' : 'origin-top',
                contentClassName,
              )}
            >
              {content}
            </HoverCardPrimitive.Content>
          ) : null}
        </HoverCardPrimitive.Portal>
      </HoverCardPrimitive.Root>
    );
  }

  return (
    <TooltipPrimitive.Provider delayDuration={0} disableHoverableContent={false}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{trigger}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content
            id={tooltipId}
            side={side}
            sideOffset={8}
            collisionPadding={8}
            className={cn(
              tooltipContentClassName,
              side === 'top' ? 'origin-bottom' : 'origin-top',
              contentClassName,
            )}
          >
            {content}
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
};
