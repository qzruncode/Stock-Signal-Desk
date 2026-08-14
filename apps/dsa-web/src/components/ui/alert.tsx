import type React from 'react';
import { cn } from '../../utils/cn';

export type AlertVariant = 'default' | 'success' | 'warning' | 'destructive';

export interface AlertProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: AlertVariant;
}

const variantClasses: Record<AlertVariant, string> = {
  default: 'border-border bg-card text-foreground',
  success: 'border-success/25 bg-success/10 text-success',
  warning: 'border-warning/25 bg-warning/10 text-warning',
  destructive: 'border-destructive/25 bg-destructive/10 text-destructive',
};

export function Alert({ className, variant = 'default', ...props }: AlertProps) {
  return (
    <div
      role="alert"
      data-slot="alert"
      className={cn('relative w-full rounded-lg border px-4 py-3 text-sm', variantClasses[variant], className)}
      {...props}
    />
  );
}
