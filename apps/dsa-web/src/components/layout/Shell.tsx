import type React from 'react';
import { useEffect, useState } from 'react';
import { BarChart3, ChevronLeft, ChevronRight, Flame, GitBranch, Globe, Home, Menu, MessageSquare, Rss, Search, Settings, Star, TrendingUp, X, Zap } from 'lucide-react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { cn } from '../../utils/cn';

type ShellProps = {
  children?: React.ReactNode;
};

export const Shell: React.FC<ShellProps> = ({ children }) => {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileDrawerState, setMobileDrawerState] = useState<'closed' | 'open' | 'closing'>('closed');
  const location = useLocation();

  const navItems = [
    { to: '/', label: 'AI 助手', icon: MessageSquare },
    { to: '/dashboard', label: '工作台', icon: Home },
    { to: '/stocks', label: '全市场股票', icon: Search },
    { to: '/portfolio', label: '管理自选股', icon: Star },
    { to: '/analysis', label: '个股分析', icon: Zap },
    { to: '/rss', label: 'RSS 资讯', icon: Rss },
    { to: '/macro', label: '宏观数据', icon: TrendingUp },
    { to: '/market', label: '市场分析', icon: Globe },
    { to: '/market-leaders', label: '市场主线', icon: Flame },
    { to: '/workflows', label: '工作流编排', icon: GitBranch },
    { to: '/settings', label: '模型 API 配置', icon: Settings },
  ];

  useEffect(() => {
    setMobileDrawerState('closed');
  }, [location.pathname]);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return undefined;
    }

    let lastStableHeight = 0;

    const isTextInputFocused = () => {
      const activeElement = document.activeElement;
      if (!activeElement) {
        return false;
      }

      return (
        activeElement instanceof HTMLInputElement ||
        activeElement instanceof HTMLTextAreaElement ||
        activeElement.getAttribute('contenteditable') === 'true'
      );
    };

    const getViewportHeight = () => {
      const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
      return Math.round(viewportHeight);
    };

    const applyHeight = (height: number) => {
      document.documentElement.style.setProperty('--app-shell-height', `${height}px`);
    };

    const syncHeight = () => {
      const nextHeight = getViewportHeight();

      if (!lastStableHeight) {
        lastStableHeight = nextHeight;
        applyHeight(nextHeight);
        return;
      }

      const keyboardLikelyOpen = isTextInputFocused() && nextHeight < lastStableHeight - 120;
      if (keyboardLikelyOpen) {
        return;
      }

      lastStableHeight = nextHeight;
      applyHeight(nextHeight);
    };

    syncHeight();
    window.addEventListener('resize', syncHeight);
    window.visualViewport?.addEventListener('resize', syncHeight);
    window.addEventListener('orientationchange', syncHeight);

    return () => {
      window.removeEventListener('resize', syncHeight);
      window.visualViewport?.removeEventListener('resize', syncHeight);
      window.removeEventListener('orientationchange', syncHeight);
    };
  }, []);

  useEffect(() => {
    if (mobileDrawerState !== 'closing') {
      return undefined;
    }

    const timeout = window.setTimeout(() => setMobileDrawerState('closed'), 180);
    return () => window.clearTimeout(timeout);
  }, [mobileDrawerState]);

  const mobileOpen = mobileDrawerState !== 'closed';
  const isChatHome = location.pathname === '/';

  return (
    <div
      className={cn(
        'overflow-hidden bg-background text-foreground selection:bg-primary/20',
        isChatHome ? 'fixed inset-0' : '',
      )}
      style={isChatHome ? undefined : { height: 'var(--app-shell-height, 100svh)' }}
    >
      <div className="mx-auto flex h-full w-full max-w-[1720px] gap-0 px-0 py-0 sm:gap-3 sm:px-4 sm:py-4 lg:px-5">
        <button
          type="button"
          onClick={() => setMobileDrawerState('open')}
          className="fixed left-3 top-3 z-40 inline-flex h-9 w-9 items-center justify-center rounded-xl border border-[#d8e1ec] bg-white/96 text-slate-700 shadow-[0_12px_24px_rgba(15,23,42,0.12)] backdrop-blur transition hover:text-slate-950 sm:h-10 sm:w-10 lg:hidden"
          aria-label="打开导航菜单"
        >
          <Menu className="h-5 w-5" />
        </button>

        <aside
          className={cn(
            'hidden min-h-0 shrink-0 flex-col rounded-[1.35rem] border border-[#d6dee8] bg-white/86 p-3 backdrop-blur-xl transition-[width] duration-200 lg:flex',
            collapsed ? 'w-[4.75rem]' : 'w-64',
          )}
          aria-label="主菜单"
        >
          <div className="mb-4 flex items-center gap-3 px-1">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-950 text-white shadow-sm">
              <BarChart3 className="h-5 w-5" />
            </div>
            {!collapsed ? (
              <div className="min-w-0">
                <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">Stock Signal</p>
                <p className="truncate text-sm font-semibold text-slate-950">Stock-Signal-Desk</p>
              </div>
            ) : null}
          </div>

          <nav className="flex flex-1 flex-col gap-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  title={collapsed ? item.label : undefined}
                  className={({ isActive }) => cn(
                    'flex h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium transition',
                    collapsed && 'justify-center px-0',
                    isActive
                      ? 'border border-cyan-200 bg-cyan-50 text-cyan-800 shadow-sm'
                      : 'text-slate-600 hover:bg-slate-100 hover:text-slate-950',
                  )}
                >
                  <Icon className="h-[18px] w-[18px] shrink-0" />
                  {!collapsed ? <span className="truncate">{item.label}</span> : null}
                </NavLink>
              );
            })}
          </nav>

          <button
            type="button"
            onClick={() => setCollapsed((value) => !value)}
            className="mt-3 flex h-10 items-center justify-center rounded-xl border border-[#d8e1ec] bg-white text-slate-500 transition hover:border-cyan-300 hover:text-cyan-700"
            aria-label={collapsed ? '展开菜单栏' : '收起菜单栏'}
            title={collapsed ? '展开菜单栏' : '收起菜单栏'}
          >
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </button>
        </aside>

        {mobileOpen ? (
          <div className="fixed inset-0 z-50 lg:hidden" onClick={() => setMobileDrawerState('closing')}>
            <div
              className={cn(
                'page-drawer-overlay absolute inset-0 backdrop-blur-[2px]',
                mobileDrawerState === 'closing' ? 'mobile-drawer-overlay-out' : 'mobile-drawer-overlay',
              )}
            />
            <aside
              className={cn(
                'absolute inset-y-0 left-0 flex w-[min(20rem,86vw)] flex-col border-r border-[#d6dee8] bg-white/96 p-3 shadow-2xl',
                mobileDrawerState === 'closing' ? 'mobile-drawer-left-out' : 'mobile-drawer-left',
              )}
              aria-label="主菜单"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="mb-4 flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-950 text-white shadow-sm">
                    <BarChart3 className="h-5 w-5" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">Stock Signal</p>
                    <p className="truncate text-sm font-semibold text-slate-950">Stock-Signal-Desk</p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setMobileDrawerState('closing')}
                  className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-[#d8e1ec] bg-white text-slate-500"
                  aria-label="关闭导航菜单"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <nav className="flex flex-1 flex-col gap-1">
                {navItems.map((item) => {
                  const Icon = item.icon;
                  return (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      className={({ isActive }) => cn(
                        'flex h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium transition',
                        isActive
                          ? 'border border-cyan-200 bg-cyan-50 text-cyan-800 shadow-sm'
                          : 'text-slate-600 hover:bg-slate-100 hover:text-slate-950',
                      )}
                    >
                      <Icon className="h-[18px] w-[18px] shrink-0" />
                      <span className="truncate">{item.label}</span>
                    </NavLink>
                  );
                })}
              </nav>
            </aside>
          </div>
        ) : null}

        <main
          className={cn(
            'min-h-0 min-w-0 flex-1 touch-pan-y overflow-x-hidden',
            isChatHome ? 'overflow-hidden' : 'overflow-y-auto',
          )}
        >
          {children ?? <Outlet />}
        </main>
      </div>
    </div>
  );
};
