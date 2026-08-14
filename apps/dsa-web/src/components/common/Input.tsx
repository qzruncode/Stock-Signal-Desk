import { forwardRef, useId, useState } from 'react';
import type React from 'react';
import { Lock, Key } from 'lucide-react';
import { cn } from '../../utils/cn';
import { EyeToggleIcon } from './EyeToggleIcon';
import { Input as ShadcnInput } from '../ui/input';
import { Label } from '../ui/label';

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: string;
  error?: string;
  /** Controls the control density for compact settings rows. */
  density?: 'regular' | 'compact';
  trailingAction?: React.ReactNode;
  /** Enables the built-in password visibility toggle. */
  allowTogglePassword?: boolean;
  /** Controls the leading icon style. */
  iconType?: 'password' | 'key' | 'none';
  /** Allows external visibility state control. */
  passwordVisible?: boolean;
  /** Notifies the parent when visibility changes in controlled mode. */
  onPasswordVisibleChange?: (visible: boolean) => void;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(({
  label,
  hint,
  error,
  density = 'regular',
  className = '',
  id,
  trailingAction,
  allowTogglePassword,
  iconType = 'none',
  passwordVisible,
  onPasswordVisibleChange,
  ...props
}, ref) => {
  const isCompact = density === 'compact';
  const generatedId = useId();
  const inputId = id ?? props.name ?? generatedId;
  const hintId = hint ? `${inputId}-hint` : undefined;
  const errorId = error ? `${inputId}-error` : undefined;
  const describedBy = [props['aria-describedby'], errorId ?? hintId].filter(Boolean).join(' ') || undefined;
  const ariaInvalid = props['aria-invalid'] ?? (error ? true : undefined);

  const [isPasswordVisible, setIsPasswordVisible] = useState(false);
  const isPasswordInput = props.type === 'password';
  const isVisibilityControlled = typeof passwordVisible === 'boolean';
  const visible = isVisibilityControlled ? passwordVisible : isPasswordVisible;
  const effectiveType = isPasswordInput && allowTogglePassword && visible ? 'text' : props.type;

  const renderLeadingIcon = () => {
    if (iconType === 'password') {
      return <Lock className="h-4 w-4 text-muted-text/55" />;
    }
    if (iconType === 'key') {
      return <Key className="h-4 w-4 text-muted-text/55" />;
    }
    return null;
  };

  const leadingIcon = renderLeadingIcon();
  const inputStyle = error
    ? {
      ...props.style,
      ['--input-surface-border-focus' as string]: 'hsla(var(--destructive), 0.4)',
      ['--input-surface-focus-ring' as string]: '0 0 0 4px hsla(var(--destructive), 0.1)',
    }
    : props.style;

  const defaultTrailingAction = isPasswordInput && allowTogglePassword ? (
    <button
      type="button"
      className={cn(
        'inline-flex h-8 w-8 items-center justify-center rounded-lg border transition-all duration-200 focus:outline-none focus:ring-2',
        visible
          ? 'border-warning/40 bg-warning/15 text-warning shadow-[0_0_10px_hsla(var(--warning),0.15)]'
          : 'border-border/40 bg-muted/20 text-muted-text hover:border-warning/40 hover:text-warning hover:shadow-[0_0_10px_hsla(var(--warning),0.15)] focus:ring-primary/30'
      )}
      onClick={() => {
        const nextVisible = !visible;
        if (!isVisibilityControlled) {
          setIsPasswordVisible(nextVisible);
        }
        onPasswordVisibleChange?.(nextVisible);
      }}
      aria-label={visible ? '隐藏内容' : '显示内容'}
      tabIndex={-1}
    >
      <EyeToggleIcon visible={visible} />
    </button>
  ) : null;

  const finalTrailingAction = trailingAction || defaultTrailingAction;

  return (
    <div className="flex flex-col">
      {label ? (
        <Label
          htmlFor={inputId}
          className={cn(
            isCompact
              ? 'mb-1 text-[13px] font-medium leading-5 text-foreground'
              : 'mb-2 text-sm font-medium text-foreground',
          )}
        >
          {label}
        </Label>
      ) : null}
      <div className="relative flex items-center">
        {leadingIcon && (
          <div className="absolute left-3.5 z-10 pointer-events-none">
            {leadingIcon}
          </div>
        )}
        <ShadcnInput
          id={inputId}
          ref={ref}
          aria-describedby={describedBy}
          aria-invalid={ariaInvalid}
          style={inputStyle}
          className={cn(
            'input-surface input-focus-glow w-full border bg-transparent transition-all',
            isCompact
              ? 'h-9 rounded-md px-3 text-xs'
              : 'h-11 rounded-xl px-4 text-sm',
            'focus:outline-none',
            error ? 'border-danger/30' : '',
            leadingIcon ? (isCompact ? 'pl-9' : 'pl-10') : '',
            finalTrailingAction ? (isCompact ? 'pr-10' : 'pr-12') : '',
            'disabled:cursor-not-allowed disabled:opacity-60',
            className,
          )}
          {...props}
          type={effectiveType}
        />
        {finalTrailingAction ? (
          <div className="absolute inset-y-0 right-2 flex items-center">
            {finalTrailingAction}
          </div>
        ) : null}
      </div>
      {error ? (
        <p
          id={errorId}
          role="alert"
          className={isCompact ? 'mt-1 text-[11px] leading-4 text-danger' : 'mt-2 text-xs text-danger'}
        >
          {error}
        </p>
      ) : hint ? (
        <p
          id={hintId}
          className={isCompact ? 'mt-1 text-[11px] leading-4 text-secondary-text' : 'mt-2 text-xs text-secondary-text'}
        >
          {hint}
        </p>
      ) : null}
    </div>
  );
});

Input.displayName = 'Input';
