import { Dialog as DialogPrimitive } from 'radix-ui';
import type React from 'react';
import { X } from 'lucide-react';
import { cn } from '../../utils/cn';

export interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  width?: string;
  /** When true, ESC/backdrop close is disabled (e.g. during screening). */
  preventClose?: boolean;
  footer?: React.ReactNode;
  className?: string;
}

/** Radix-backed centered dialog with the existing Modal API. */
export const Modal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  title,
  children,
  width = 'max-w-2xl',
  preventClose = false,
  footer,
  className,
}) => {
  const handleOpenChange = (open: boolean) => {
    if (!open && !preventClose) onClose();
  };

  const preventDismiss = (event: { preventDefault: () => void }) => {
    if (preventClose) event.preventDefault();
  };

  if (!isOpen) return null;

  return (
    <DialogPrimitive.Root open onOpenChange={handleOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm data-[state=closed]:animate-out data-[state=open]:animate-in data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          aria-label={title}
          onEscapeKeyDown={preventDismiss}
          onPointerDownOutside={preventDismiss}
          onInteractOutside={preventDismiss}
          className={cn(
            'fixed left-1/2 top-1/2 z-50 max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-border/70 bg-elevated p-6 text-secondary-text shadow-2xl outline-none data-[state=closed]:animate-out data-[state=open]:animate-in data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95',
            width,
            className,
          )}
        >
          <DialogPrimitive.Title asChild>
            {title ? (
              <h3 className="mb-4 pr-8 text-lg font-medium text-foreground">{title}</h3>
            ) : (
              <span className="sr-only">对话框</span>
            )}
          </DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">
            {title ? `${title}窗口` : '对话框'}
          </DialogPrimitive.Description>

          {!preventClose ? (
            <DialogPrimitive.Close asChild>
              <button
                type="button"
                className="absolute right-4 top-4 inline-flex size-8 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 focus:outline-none focus:ring-2 focus:ring-cyan/20"
                aria-label="关闭"
              >
                <X className="size-5" />
              </button>
            </DialogPrimitive.Close>
          ) : null}

          <div>{children}</div>

          {footer ? <div className="mt-4 border-t border-border/50 pt-4">{footer}</div> : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
};
