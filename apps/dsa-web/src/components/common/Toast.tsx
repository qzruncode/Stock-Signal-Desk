import { useCallback, useRef, useState } from 'react';
import type React from 'react';
import { CheckCircle2, CircleAlert, Info, X } from 'lucide-react';
import {
  Toast as UiToast,
  ToastClose,
  ToastDescription,
  ToastProvider as UiToastProvider,
  ToastTitle,
  ToastViewport,
  type ToastVariant,
} from '../ui/toast';
import { ToastContext, type ToastInput } from './ToastContext';

interface ToastItem extends ToastInput {
  id: string;
}

const ToastIcon: React.FC<{ variant: ToastVariant }> = ({ variant }) => {
  if (variant === 'success') return <CheckCircle2 className="size-3.5 shrink-0 text-success" aria-hidden="true" />;
  if (variant === 'error') return <CircleAlert className="size-3.5 shrink-0 text-danger" aria-hidden="true" />;
  if (variant === 'info') return <Info className="size-3.5 shrink-0 text-primary" aria-hidden="true" />;
  return null;
};

export const AppToastProvider: React.FC<React.PropsWithChildren> = ({ children }) => {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(0);

  const dismissToast = useCallback((id: string) => {
    setToasts((current) => current.filter((item) => item.id !== id));
  }, []);

  const toast = useCallback((input: ToastInput) => {
    const id = 'toast-' + nextId.current++;
    setToasts((current) => [...current.slice(-2), { ...input, id }]);
    return id;
  }, []);

  return (
    <ToastContext.Provider value={{ toast, dismissToast }}>
      <UiToastProvider label="消息提示" swipeDirection="right">
        {children}
        <ToastViewport>
          {toasts.map((item) => {
            const variant = item.variant ?? 'default';
            return (
              <UiToast
                key={item.id}
                open
                duration={item.duration ?? 3600}
                variant={variant}
                onOpenChange={(open) => {
                  if (!open) dismissToast(item.id);
                }}
              >
                <ToastIcon variant={variant} />
                <div className="flex min-w-0 max-w-full flex-1 items-center gap-1.5">
                  <ToastTitle>{item.title}</ToastTitle>
                  {item.description ? (
                    <ToastDescription className="truncate leading-4">
                      · {item.description}
                    </ToastDescription>
                  ) : null}
                </div>
                <ToastClose aria-label="关闭提示" className="static shrink-0 opacity-100">
                  <X className="size-4" />
                </ToastClose>
              </UiToast>
            );
          })}
        </ToastViewport>
      </UiToastProvider>
    </ToastContext.Provider>
  );
};
