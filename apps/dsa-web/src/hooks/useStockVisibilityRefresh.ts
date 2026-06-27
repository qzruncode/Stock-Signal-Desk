import { useEffect } from 'react';

type OnVisible = () => void;

/**
 * useStockVisibilityRefresh Hook
 *
 * Invoke `onVisible` whenever the document becomes visible or the window
 * gains focus. Useful for re-syncing data when the user returns to the tab
 * (e.g. after same-tab navigation or switching back from another tab).
 *
 * @param onVisible - void callback fired on `visibilitychange` (visible state) and `focus`
 */
export function useStockVisibilityRefresh(onVisible: OnVisible): void {
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        onVisible();
      }
    };
    const handleFocus = () => {
      onVisible();
    };

    document.addEventListener('visibilitychange', handleVisibility);
    window.addEventListener('focus', handleFocus);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      window.removeEventListener('focus', handleFocus);
    };
  }, [onVisible]);
}

export default useStockVisibilityRefresh;
