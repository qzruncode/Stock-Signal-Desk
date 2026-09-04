import { HoverCard as HoverCardPrimitive, Tooltip as TooltipPrimitive } from 'radix-ui';
import type React from 'react';
import { useId } from 'react';
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
  'pointer-events-auto z-[120] min-w-max max-w-[18rem] rounded-xl border border-border/70 bg-elevated/95 px-3 py-1.5 text-xs leading-5 text-foreground shadow-[0_16px_40px_rgba(3,8,20,0.18)] backdrop-blur-xl';

/**
 * Shared hover surface. Radix owns the trigger/content handoff and pointer
 * grace area so a user can move into the floating content without racing a
 * local close timer.
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
      <HoverCardPrimitive.Root openDelay={0} closeDelay={500}>
        <HoverCardPrimitive.Trigger asChild>{trigger}</HoverCardPrimitive.Trigger>
        <HoverCardPrimitive.Portal>
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
