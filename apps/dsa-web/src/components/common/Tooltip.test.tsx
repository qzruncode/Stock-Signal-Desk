import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Tooltip } from './Tooltip';

describe('Tooltip', () => {
  it('keeps the detail open while the pointer moves from trigger to content', () => {
    vi.useFakeTimers();

    try {
      render(
        <Tooltip content="证据详情" focusable ariaLabel="查看详情">
          <span>①</span>
        </Tooltip>,
      );

      const trigger = screen.getByLabelText('查看详情');
      fireEvent.pointerMove(trigger);
      act(() => {
        vi.advanceTimersByTime(0);
      });
      const tooltip = screen.getByRole('tooltip');

      fireEvent.pointerLeave(trigger);
      act(() => {
        vi.advanceTimersByTime(100);
      });
      expect(screen.getByRole('tooltip')).toBeInTheDocument();

      fireEvent.pointerEnter(tooltip);
      act(() => {
        vi.advanceTimersByTime(200);
      });
      expect(screen.getByRole('tooltip')).toBeInTheDocument();
      expect(tooltip).toHaveClass('pointer-events-auto');

      fireEvent.pointerLeave(tooltip);
      act(() => {
        fireEvent.pointerMove(document.body, { clientX: 1000, clientY: 1000 });
        vi.runOnlyPendingTimers();
      });
      expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps interactive content open while the pointer is inside the detail layer', () => {
    vi.useFakeTimers();

    try {
      render(
        <Tooltip interactive content={<button type="button">复制证据</button>} focusable ariaLabel="查看详情">
          <span>①</span>
        </Tooltip>,
      );

      const trigger = screen.getByLabelText('查看详情');
      fireEvent.pointerEnter(trigger);
      act(() => {
        vi.advanceTimersByTime(0);
      });

      const tooltip = screen.getByRole('tooltip');
      expect(screen.getByRole('button', { name: '复制证据' })).toBeInTheDocument();

      fireEvent.pointerLeave(trigger);
      fireEvent.pointerEnter(tooltip);
      act(() => {
        vi.advanceTimersByTime(5000);
      });
      expect(screen.getByRole('tooltip')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '复制证据' })).toBeInTheDocument();

      fireEvent.pointerLeave(tooltip);
      act(() => {
        vi.advanceTimersByTime(500);
      });
      expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
