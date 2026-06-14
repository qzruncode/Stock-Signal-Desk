import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { Shell } from '../Shell';

describe('Shell', () => {
  it('renders the workspace with collapsible primary navigation', () => {
    render(
      <MemoryRouter>
        <Shell>
          <div>page content</div>
        </Shell>
      </MemoryRouter>,
    );

    expect(screen.getByText('page content')).toBeInTheDocument();
    expect(screen.getByRole('navigation')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '工作台' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '管理自选股' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '市场主线' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '收起菜单栏' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '切换主题' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '持仓' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '回测' })).not.toBeInTheDocument();
  });

  it('opens the mobile navigation drawer from the menu button', () => {
    render(
      <MemoryRouter>
        <Shell>
          <div>page content</div>
        </Shell>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: '打开导航菜单' }));

    expect(screen.getByRole('button', { name: '关闭导航菜单' })).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: '工作台' }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('link', { name: '模型 API 配置' }).length).toBeGreaterThan(0);
  });
});
