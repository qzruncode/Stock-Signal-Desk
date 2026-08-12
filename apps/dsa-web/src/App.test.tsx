import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

const authState = vi.hoisted(() => ({
  authEnabled: false,
  loggedIn: false,
  isLoading: false,
  loadError: {
    title: '请求失败',
    message: '认证状态暂不可用',
    rawMessage: 'HTTP 500',
    status: 500,
    category: 'http_error' as const,
  } as { title: string; message: string; rawMessage: string; status: number; category: 'http_error' } | null,
  refreshStatus: vi.fn(),
}));

vi.mock('./contexts/AuthContext', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useAuth: () => authState,
}));

vi.mock('./components/common', async () => {
  const { Outlet } = await import('react-router-dom');
  return { Shell: () => <Outlet /> };
});

vi.mock('./utils/routePreload', () => ({
  scheduleIdlePreload: vi.fn(),
  ChatHomePage: () => <div>聊天页面仍可使用</div>,
  LoginPage: () => <div data-testid="login-page">登录页面</div>,
  NotFoundPage: () => <div>未找到页面</div>,
  RunExplorerPage: () => <div>运行记录</div>,
  SettingPage: () => <div>设置页面</div>,
}));

import App from './App';

describe('App', () => {
  it('fails closed when auth status cannot be loaded', () => {
    render(<App />);

    expect(screen.getByRole('alert')).toHaveTextContent('认证状态暂不可用');
    expect(screen.queryByText('聊天页面仍可使用')).not.toBeInTheDocument();
  });

  it('routes an unauthenticated user to the login page', () => {
    authState.loadError = null;
    authState.authEnabled = true;
    authState.loggedIn = false;

    render(<App />);

    expect(screen.getByTestId('login-page')).toBeInTheDocument();
  });
});
