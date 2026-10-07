import { forwardRef } from 'react';
import type React from 'react';
import { cn } from '../../utils/cn';

export const Card = forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      data-slot="card"
      className={cn('rounded-lg border border-border bg-card text-card-foreground shadow-sm', className)}
      {...props}
    />
  ),
);

Card.displayName = 'Card';
