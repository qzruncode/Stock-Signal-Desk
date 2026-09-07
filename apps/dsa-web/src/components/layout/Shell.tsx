import type React from 'react';
import { useEffect } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { cn } from '../../utils/cn';
import { WorkspaceNav } from './WorkspaceNav';

type ShellProps = {
  children?: React.ReactNode;
};

/**
 * Stable viewport with workspace navigation outside the full-screen chat.
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
    document.documentElement.classList.add('app-shell-lock-scroll');
    document.body.classList.add('app-shell-lock-scroll');

    return () => {
      document.documentElement.classList.remove('app-shell-lock-scroll');
      document.body.classList.remove('app-shell-lock-scroll');
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
        'flex w-full flex-col overflow-hidden bg-background text-foreground selection:bg-primary/20',
        isChatHome && 'fixed inset-0',
      )}
      style={isChatHome ? undefined : { height: 'var(--app-shell-height, 100svh)' }}
    >
      {!isChatHome && <WorkspaceNav />}
      <main className={cn('min-h-0 min-w-0 flex-1', isChatHome ? 'overflow-hidden' : 'overflow-y-auto px-4')}>
        {children ?? <Outlet />}
      </main>
    </div>
  );
};
