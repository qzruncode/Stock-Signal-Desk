import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('./contexts/AuthContext', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useAuth: () => ({
    isLoading: false,
    loadError: {
      title: '请求失败',
      message: '认证状态暂不可用',
      rawMessage: 'HTTP 500',
      status: 500,
      category: 'http_error',
    },
  }),
}));

vi.mock('./components/common', async () => {
  const { Outlet } = await import('react-router-dom');
  return { Shell: () => <Outlet /> };
});

vi.mock('./utils/routePreload', () => ({
  scheduleIdlePreload: vi.fn(),
  ChatHomePage: () => <div>聊天页面仍可使用</div>,
  NotFoundPage: () => <div>未找到页面</div>,
  RunExplorerPage: () => <div>运行记录</div>,
  SettingPage: () => <div>设置页面</div>,
}));

import App from './App';

describe('App', () => {
  it('does not replace the chat page when a transient auth-status request fails', () => {
    render(<App />);

    expect(screen.getByText('聊天页面仍可使用')).toBeInTheDocument();
    expect(screen.queryByText('认证状态暂不可用')).not.toBeInTheDocument();
  });
});
