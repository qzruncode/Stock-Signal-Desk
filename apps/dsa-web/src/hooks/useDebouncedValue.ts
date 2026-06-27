import { useEffect, useState } from 'react';

/**
 * Returns a debounced copy of `value` that only updates after `delay`
 * milliseconds have elapsed without further changes.
 *
 * Extracts the `setTimeout` / `clearTimeout` pattern used for input
 * debouncing (e.g. `RssPage.tsx`) into a reusable primitive that works
 * for any string value.
 *
 * @param value  The live string value to debounce.
 * @param delay  Milliseconds to wait before propagating the value.
 * @returns      The latest value after the debounce window has settled.
 */
export function useDebouncedValue(value: string, delay = 500): string {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);

  return debouncedValue;
}

export default useDebouncedValue;
