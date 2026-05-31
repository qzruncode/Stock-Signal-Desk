import { useCallback, useEffect, useRef, useState } from 'react';

export function useTransientMessage(timeoutMs = 2000) {
  const [message, setMessage] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const clearMessage = useCallback(() => {
    clearTimer();
    setMessage(null);
  }, [clearTimer]);

  const showMessage = useCallback((nextMessage: string) => {
    clearTimer();
    setMessage(nextMessage);
    timerRef.current = window.setTimeout(() => {
      setMessage(null);
      timerRef.current = null;
    }, timeoutMs);
  }, [clearTimer, timeoutMs]);

  useEffect(() => clearTimer, [clearTimer]);

  return {
    message,
    showMessage,
    clearMessage,
  };
}
