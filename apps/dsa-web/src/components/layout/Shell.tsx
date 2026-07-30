import type React from 'react';
import { useEffect } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { cn } from '../../utils/cn';

type ShellProps = {
  children?: React.ReactNode;
};

/**
 * Product shell for a single-purpose AI assistant.
 *
 * Global feature navigation intentionally lives inside the assistant itself;
 * this wrapper only owns the stable viewport and route content.
 */
export const Shell: React.FC<ShellProps> = ({ children }) => {
  const location = useLocation();
  const isChatHome = location.pathname === '/';

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    let lastStableHeight = 0;

    const isTextInputFocused = () => {
      const activeElement = document.activeElement;
      return activeElement instanceof HTMLInputElement
        || activeElement instanceof HTMLTextAreaElement
        || activeElement?.getAttribute('contenteditable') === 'true';
    };
    const viewportHeight = () => Math.round(window.visualViewport?.height ?? window.innerHeight);
    const syncHeight = () => {
      const nextHeight = viewportHeight();
      if (lastStableHeight && isTextInputFocused() && nextHeight < lastStableHeight - 120) return;
      lastStableHeight = nextHeight;
      document.documentElement.style.setProperty('--app-shell-height', `${nextHeight}px`);
    };

    syncHeight();
    window.addEventListener('resize', syncHeight);
    window.visualViewport?.addEventListener('resize', syncHeight);
    window.addEventListener('orientationchange', syncHeight);
    return () => {
      window.removeEventListener('resize', syncHeight);
      window.visualViewport?.removeEventListener('resize', syncHeight);
      window.removeEventListener('orientationchange', syncHeight);
    };
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia('(pointer: coarse)').matches) {
      return undefined;
    }

    const preventZoom = (event: Event) => {
      event.preventDefault();
    };

    document.addEventListener('gesturestart', preventZoom, { passive: false });
    document.addEventListener('gesturechange', preventZoom, { passive: false });
    document.addEventListener('gestureend', preventZoom, { passive: false });

    return () => {
      document.removeEventListener('gesturestart', preventZoom);
      document.removeEventListener('gesturechange', preventZoom);
      document.removeEventListener('gestureend', preventZoom);
    };
  }, []);

  return (
    <div
      className={cn(
        'w-full overflow-hidden bg-background text-foreground selection:bg-primary/20',
        isChatHome && 'fixed inset-0',
      )}
      style={isChatHome ? undefined : { height: 'var(--app-shell-height, 100svh)' }}
    >
      <main className={cn('h-full min-h-0 min-w-0', isChatHome ? 'overflow-hidden' : 'overflow-y-auto px-4')}>
        {children ?? <Outlet />}
      </main>
    </div>
  );
};
