import type React from 'react';
import { useEffect, useCallback } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { cn } from '../../utils/cn';

let activeDrawerCount = 0;

interface DrawerProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  eyebrow?: string | null;
  children: React.ReactNode;
  width?: string;
  zIndex?: number;
  side?: 'left' | 'right';
  backdropClassName?: string;
}

/**
 * Side drawer component with terminal-inspired styling.
 */
export const Drawer: React.FC<DrawerProps> = ({
  isOpen,
  onClose,
  title,
  eyebrow = 'DETAIL VIEW',
  children,
  width = 'max-w-2xl',
  zIndex = 50,
  side = 'right',
  backdropClassName,
}) => {
  const prefersReducedMotion = useReducedMotion();

  // Close the drawer when Escape is pressed.
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    },
    [onClose]
  );

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
      activeDrawerCount++;
      if (activeDrawerCount === 1) {
        document.body.style.overflow = 'hidden';
      }

      return () => {
        document.removeEventListener('keydown', handleKeyDown);
        activeDrawerCount--;
        if (activeDrawerCount === 0) {
          document.body.style.overflow = '';
        }
      };
    }
  }, [isOpen, handleKeyDown]);

  const titleId = title ? `drawer-title-${side}` : undefined;
  const sidePositionClass = side === 'left' ? 'left-0 justify-start' : 'right-0 justify-end';
  const borderClass = side === 'left' ? 'border-r' : 'border-l';
  const closedOffset = prefersReducedMotion ? 0 : side === 'left' ? '-100%' : '100%';

  return (
    <AnimatePresence>
      {isOpen ? (
        <motion.div
          className="fixed inset-0 overflow-hidden"
          style={{ zIndex }}
          role="presentation"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: prefersReducedMotion ? 0 : 0.22, ease: 'easeOut' }}
        >
          <div
            className={cn(
              'absolute inset-0 bg-background/70 backdrop-blur-sm',
              backdropClassName,
            )}
            onClick={onClose}
          />

          <div className={cn('absolute inset-y-0 flex w-full', sidePositionClass, width)}>
            <motion.div
              role="dialog"
              aria-modal="true"
              aria-labelledby={titleId}
              initial={{ x: closedOffset }}
              animate={{ x: 0 }}
              exit={{ x: closedOffset }}
              transition={{
                duration: prefersReducedMotion ? 0 : 0.3,
                ease: [0.22, 1, 0.36, 1],
              }}
              className={cn(
                'relative flex w-full flex-col bg-card shadow-2xl',
                borderClass,
                side === 'right' ? 'border-border/80' : 'border-border/70',
              )}
            >
              <div className="flex items-center justify-between border-b border-border/60 px-6 py-4">
                {title ? (
                  <div className="min-w-0">
                    {eyebrow ? <span className="label-uppercase">{eyebrow}</span> : null}
                    <h2
                      id={titleId}
                      className={cn(
                        'truncate text-lg font-semibold text-foreground',
                        eyebrow && 'mt-1',
                      )}
                    >
                      {title}
                    </h2>
                  </div>
                ) : <div />}
                <button
                  type="button"
                  onClick={onClose}
                  className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border/70 bg-card/80 text-secondary-text transition-all duration-200 hover:bg-hover hover:text-foreground active:scale-90"
                  aria-label="关闭抽屉"
                >
                  <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
              <div className="flex-1 overflow-y-auto p-6">
                {children}
              </div>
            </motion.div>
          </div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
};
