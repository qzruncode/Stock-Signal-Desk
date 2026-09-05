import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Badge } from './Badge';

describe('Badge', () => {
  it('keeps a label on one line inside responsive layouts', () => {
    render(<Badge variant="danger">47 风险分</Badge>);

    expect(screen.getByText('47 风险分')).toHaveClass('shrink-0', 'whitespace-nowrap');
  });
});
