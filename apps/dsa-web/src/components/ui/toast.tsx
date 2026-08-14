import { forwardRef } from 'react';
import type React from 'react';
import { Toast as ToastPrimitive } from 'radix-ui';
import { cn } from '../../utils/cn';

export const ToastProvider = ToastPrimitive.Provider;

export const ToastViewport = forwardRef<
  React.ElementRef<typeof ToastPrimitive.Viewport>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitive.Viewport>
>(({ className, ...props }, ref) => (
  <ToastPrimitive.Viewport
    ref={ref}
    data-slot="toast-viewport"
    className={cn(
      'fixed bottom-4 right-4 z-[100] flex max-h-screen w-auto max-w-[min(440px,calc(100vw-2rem))] flex-col items-end gap-2 outline-none',
      className,
    )}
    {...props}
  />
));

ToastViewport.displayName = ToastPrimitive.Viewport.displayName;

export type ToastVariant = 'default' | 'success' | 'error' | 'info';

export interface ToastProps extends React.ComponentPropsWithoutRef<typeof ToastPrimitive.Root> {
  variant?: ToastVariant;
}

const variantClasses: Record<ToastVariant, string> = {
  default: 'border-border bg-card text-card-foreground',
  success: 'border-success/30 bg-card text-foreground',
  error: 'border-destructive/30 bg-card text-foreground',
  info: 'border-primary/30 bg-card text-foreground',
};

export const Toast = forwardRef<
  React.ElementRef<typeof ToastPrimitive.Root>,
  ToastProps
>(({ className, variant = 'default', ...props }, ref) => (
  <ToastPrimitive.Root
    ref={ref}
    data-slot="toast"
    data-variant={variant}
    className={cn(
      'group pointer-events-auto relative flex w-fit max-w-full items-center gap-2 overflow-hidden rounded-md border px-3 py-2 shadow-md',
      'transition-all data-[state=closed]:animate-out data-[state=closed]:fade-out-80 data-[state=closed]:slide-out-to-right-full',
      'data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:slide-in-from-bottom-2',
      variantClasses[variant],
      className,
    )}
    {...props}
  />
));

Toast.displayName = ToastPrimitive.Root.displayName;

export const ToastTitle = forwardRef<
  React.ElementRef<typeof ToastPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitive.Title>
>(({ className, ...props }, ref) => (
  <ToastPrimitive.Title ref={ref} data-slot="toast-title" className={cn('shrink-0 text-xs font-semibold', className)} {...props} />
));

ToastTitle.displayName = ToastPrimitive.Title.displayName;

export const ToastDescription = forwardRef<
  React.ElementRef<typeof ToastPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitive.Description>
>(({ className, ...props }, ref) => (
  <ToastPrimitive.Description ref={ref} data-slot="toast-description" className={cn('min-w-0 truncate text-xs text-muted-foreground', className)} {...props} />
));

ToastDescription.displayName = ToastPrimitive.Description.displayName;

export const ToastClose = forwardRef<
  React.ElementRef<typeof ToastPrimitive.Close>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitive.Close>
>(({ className, ...props }, ref) => (
  <ToastPrimitive.Close
    ref={ref}
    data-slot="toast-close"
    className={cn(
      'absolute right-2 top-2 rounded-md p-1 text-muted-foreground opacity-0 transition-opacity',
      'hover:bg-accent hover:text-accent-foreground focus:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring',
      'group-hover:opacity-100',
      className,
    )}
    {...props}
  />
));

ToastClose.displayName = ToastPrimitive.Close.displayName;
