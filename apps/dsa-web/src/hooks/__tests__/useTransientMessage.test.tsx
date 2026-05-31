import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useTransientMessage } from '../useTransientMessage';

describe('useTransientMessage', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('keeps one reset timer and clears it on unmount', () => {
    const { result, unmount } = renderHook(() => useTransientMessage(2000));

    act(() => {
      result.current.showMessage('first');
      result.current.showMessage('second');
    });

    expect(result.current.message).toBe('second');
    expect(vi.getTimerCount()).toBe(1);

    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('clears the message after the configured delay', () => {
    const { result } = renderHook(() => useTransientMessage(500));

    act(() => {
      result.current.showMessage('saved');
    });

    act(() => {
      vi.advanceTimersByTime(500);
    });

    expect(result.current.message).toBeNull();
  });
});
