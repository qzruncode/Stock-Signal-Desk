import type { FC, ReactNode, ButtonHTMLAttributes } from 'react';
import { Tooltip } from '../common/Tooltip';
import { cn } from '../../utils/cn';

interface TooltipIconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  tooltip: string;
  children: ReactNode;
  variant?: 'ghost' | 'outline';
}

export const TooltipIconButton: FC<TooltipIconButtonProps> = ({
  tooltip,
  children,
  variant = 'ghost',
  className,
  ...props
}) => (
  <Tooltip content={tooltip}>
    <button
      type="button"
      className={cn(
        'inline-flex size-8 items-center justify-center rounded-lg transition-colors',
        variant === 'ghost' && 'text-muted-foreground hover:bg-accent hover:text-foreground',
        variant === 'outline' && 'border border-border text-muted-foreground hover:bg-accent hover:text-foreground',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  </Tooltip>
);
