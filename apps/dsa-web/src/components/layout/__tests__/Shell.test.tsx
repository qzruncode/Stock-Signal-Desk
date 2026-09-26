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

  it('exposes workspace navigation outside the full-screen chat', () => {
    render(<MemoryRouter initialEntries={['/runs']}><Shell><div>run content</div></Shell></MemoryRouter>);
    expect(screen.getByRole('navigation')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '运行记录' })).toHaveAttribute('href', '/runs');
  });

  it('provides an active workspace navigation item for knowledge management', () => {
    render(<MemoryRouter initialEntries={['/knowledge']}><Shell><div>knowledge content</div></Shell></MemoryRouter>);
    expect(screen.getByRole('link', { name: '知识库' })).toHaveAttribute('href', '/knowledge');
    expect(screen.getByRole('link', { name: '知识库' })).toHaveAttribute('aria-current', 'page');
  });
});
