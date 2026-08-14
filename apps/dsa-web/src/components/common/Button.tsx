import { forwardRef } from 'react';
import type React from 'react';
import { Loader2 } from 'lucide-react';
import { Button as ShadcnButton, type ButtonSize, type ButtonVariant } from '../ui/button';
import { cn } from '../../utils/cn';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline';
  size?: 'sm' | 'md' | 'lg';
  isLoading?: boolean;
  /** Custom loading text. */
  loadingText?: string;
  glow?: boolean;
}

const variantMap: Record<NonNullable<ButtonProps['variant']>, ButtonVariant> = {
  primary: 'default',
  secondary: 'secondary',
  ghost: 'ghost',
  danger: 'destructive',
  outline: 'outline',
};

const sizeMap: Record<NonNullable<ButtonProps['size']>, ButtonSize> = {
  sm: 'sm',
  md: 'default',
  lg: 'lg',
};

/** Backwards-compatible project button backed by the shadcn/ui primitive. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(({
  children,
  variant = 'primary',
  size = 'md',
  isLoading = false,
  loadingText = '处理中...',
  glow = false,
  className = '',
  disabled,
  type = 'button',
  ...props
}, ref) => (
  <ShadcnButton
    ref={ref}
    {...props}
    type={type}
    variant={variantMap[variant]}
    size={sizeMap[size]}
    aria-busy={isLoading || undefined}
    data-variant={variant}
    disabled={disabled || isLoading}
    className={cn(glow && 'shadow-glow-cyan', isLoading && 'opacity-100', className)}
  >
    {isLoading ? (
      <>
        <Loader2 className="size-4 animate-spin" aria-hidden="true" />
        {loadingText}
      </>
    ) : (
      children
    )}
  </ShadcnButton>
));

Button.displayName = 'Button';
