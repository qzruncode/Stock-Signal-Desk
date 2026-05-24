import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { Shell } from '../Shell';

describe('Shell', () => {
  it('renders the focused single-page workspace without legacy navigation controls', () => {
    render(
      <MemoryRouter>
        <Shell>
          <div>page content</div>
        </Shell>
      </MemoryRouter>,
    );

    expect(screen.getByText('page content')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '切换主题' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '持仓' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '回测' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '设置' })).not.toBeInTheDocument();
  });
});
