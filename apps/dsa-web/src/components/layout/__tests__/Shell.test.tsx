import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { Shell } from '../Shell';

describe('Shell', () => {
  it('renders route content without a global feature menu', () => {
    render(
      <MemoryRouter>
        <Shell>
          <div>page content</div>
        </Shell>
      </MemoryRouter>,
    );

    expect(screen.getByText('page content')).toBeInTheDocument();
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '打开导航菜单' })).not.toBeInTheDocument();
  });
});
