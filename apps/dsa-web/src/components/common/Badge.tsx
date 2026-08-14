import React from 'react';
import { cn } from '../../utils/cn';
import { Badge as ShadcnBadge, type BadgeVariant as ShadcnBadgeVariant } from '../ui/badge';

type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info' | 'history';

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  children: React.ReactNode;
  variant?: BadgeVariant;
  size?: 'sm' | 'md';
  glow?: boolean;
  className?: string;
  style?: React.CSSProperties;
}

const variantMap: Record<BadgeVariant, ShadcnBadgeVariant> = {
  default: 'outline',
  success: 'success',
  warning: 'warning',
  danger: 'danger',
  info: 'info',
  history: 'info',
};

/**
 * Badge component with multiple variants and optional glow styling.
 */
export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = 'default',
  size = 'sm',
  glow = false,
  className = '',
  style,
  ...rest
}) => {
  const sizeStyles = size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-3 py-1 text-sm';

  return (
    <ShadcnBadge
      {...rest}
      style={style}
      variant={variantMap[variant]}
      className={cn('gap-1 font-medium', sizeStyles, glow && 'shadow-sm', className)}
    >
      {children}
    </ShadcnBadge>
  );
};
