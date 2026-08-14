import type React from 'react';
import { cn } from '../../utils/cn';
import { Card as ShadcnCard } from '../ui/card';

interface CardProps {
  title?: string;
  subtitle?: string;
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  variant?: 'default' | 'bordered' | 'gradient';
  hoverable?: boolean;
  padding?: 'none' | 'sm' | 'md' | 'lg';
}

/** Backwards-compatible project card backed by the shadcn/ui surface primitive. */
export const Card: React.FC<CardProps> = ({
  title,
  subtitle,
  children,
  className = '',
  style,
  variant = 'default',
  hoverable = false,
  padding = 'md',
}) => {
  const paddingStyles = {
    none: '',
    sm: 'p-4',
    md: 'p-5',
    lg: 'p-6',
  };

  const variantStyles = {
    default: '',
    bordered: 'bg-card',
    gradient: 'border-primary/20',
  };

  const hoverStyles = hoverable
    ? 'cursor-pointer transition-shadow hover:border-primary/30 hover:shadow-md'
    : '';

  if (variant === 'gradient') {
    return (
      <ShadcnCard className={cn(variantStyles.gradient, className)} style={style}>
        <div className={cn(paddingStyles[padding])}>
          {(title || subtitle) && (
            <div className="mb-3">
              {subtitle ? <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{subtitle}</span> : null}
              {title ? <h3 className="mt-1 text-lg font-semibold text-foreground">{title}</h3> : null}
            </div>
          )}
          {children}
        </div>
      </ShadcnCard>
    );
  }

  return (
    <ShadcnCard
      style={style}
      className={cn(variantStyles[variant], hoverStyles, paddingStyles[padding], className)}
    >
      {(title || subtitle) && (
        <div className="mb-3">
          {subtitle ? <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{subtitle}</span> : null}
          {title ? <h3 className="mt-1 text-lg font-semibold text-foreground">{title}</h3> : null}
        </div>
      )}
      {children}
    </ShadcnCard>
  );
};
