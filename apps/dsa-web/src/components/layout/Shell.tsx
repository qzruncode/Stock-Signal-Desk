import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { BarChart3, ChevronLeft, ChevronRight, Flame, GitBranch, Globe, Home, Menu, MessageSquare, Rss, Search, Settings, Star, TrendingUp, Wrench, X, Zap } from 'lucide-react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { cn } from '../../utils/cn';
import { preloadRoute } from '../../App';

type ShellProps = {
  children?: React.ReactNode;
};

export const Shell: React.FC<ShellProps> = ({ children }) => {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileDrawerState, setMobileDrawerState] = useState<'closed' | 'open' | 'closing'>('closed');
  const location = useLocation();

  const navItems = [
    { to: '/', label: 'AI 助手', icon: MessageSquare },
    { to: '/stocks', label: '全市场股票', icon: Search },
    { to: '/dashboard', label: '工作台', icon: Home },
    { to: '/portfolio', label: '管理自选股', icon: Star },
    { to: '/analysis', label: '个股分析', icon: Zap },
    { to: '/rss', label: 'RSS 资讯', icon: Rss },
    { to: '/macro', label: '宏观数据', icon: TrendingUp },
    { to: '/market', label: '市场分析', icon: Globe },
    { to: '/market-leaders', label: '市场主线', icon: Flame },
    { to: '/workflows', label: '工作流编排', icon: GitBranch },
    { to: '/settings', label: '模型 API 配置', icon: Settings },
    { to: '/setting', label: '设置', icon: Wrench },
  ];

  const closeMobileDrawer = useCallback(() => {
    setMobileDrawerState('closed');
  }, []);

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
      <div className="mx-auto flex h-full w-full gap-0 px-0 py-0 sm:gap-3">
        <button
          type="button"
          onClick={() => setMobileDrawerState('open')}
          className="fixed left-3 top-3 z-40 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-card text-foreground/70 shadow-sm backdrop-blur transition hover:text-foreground sm:h-10 sm:w-10 lg:hidden"
          aria-label="打开导航菜单"
        >
          <Menu className="h-5 w-5" />
        </button>

        <aside
          className={cn(
            'hidden min-h-0 shrink-0 flex-col rounded-lg border border-border bg-card p-3 transition-[width] duration-200 lg:flex',
            collapsed ? 'w-[4.75rem]' : 'w-64',
          )}
          aria-label="主菜单"
        >
          <div className="mb-4 flex items-center gap-3 px-1">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <BarChart3 className="h-[18px] w-[18px]" />
            </div>
            {!collapsed ? (
              <div className="min-w-0">
                <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-muted-foreground">Stock Assistant</p>
                <p className="truncate text-sm font-semibold text-foreground">Stock Assistant</p>
              </div>
            ) : null}
          </div>

          <nav className="flex flex-1 flex-col gap-0.5">
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  aria-label={collapsed ? item.label : undefined}
                  onClick={closeMobileDrawer}
                  onMouseEnter={() => preloadRoute(item.to)}
                  className={({ isActive }) => cn(
                    'group relative flex h-9 items-center gap-3 rounded-md px-3 text-[13px] font-medium transition',
                    collapsed && 'justify-center px-0',
                    isActive
                      ? 'bg-primary/10 text-primary'
                      : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                  )}
                >
                  {({ isActive }) => (
                    <>
                      {isActive && (
                        <span className="absolute left-0 top-1/2 h-4 w-[2px] -translate-y-1/2 rounded-full bg-primary" />
                      )}
                      <Icon className="h-[17px] w-[17px] shrink-0" />
                      {!collapsed ? (
                        <span className="truncate">{item.label}</span>
                      ) : (
                        <span className="invisible absolute left-full ml-2 whitespace-nowrap rounded-md border border-border bg-popover px-2 py-1 text-xs text-foreground shadow-md group-hover:visible">
                          {item.label}
                        </span>
                      )}
                    </>
                  )}
                </NavLink>
              );
            })}
          </nav>

          <button
            type="button"
            onClick={() => setCollapsed((value) => !value)}
            className="mt-3 flex h-9 items-center justify-center rounded-md border border-border bg-card text-muted-foreground transition hover:border-primary/40 hover:text-primary"
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
                'absolute inset-y-0 left-0 flex w-[min(20rem,86vw)] flex-col border-r border-border bg-card p-3 shadow-lg',
                mobileDrawerState === 'closing' ? 'mobile-drawer-left-out' : 'mobile-drawer-left',
              )}
              aria-label="主菜单"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="mb-4 flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
                    <BarChart3 className="h-[18px] w-[18px]" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-muted-foreground">Stock Assistant</p>
                    <p className="truncate text-sm font-semibold text-foreground">Stock Assistant</p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setMobileDrawerState('closing')}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-border bg-card text-muted-foreground"
                  aria-label="关闭导航菜单"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <nav className="flex flex-1 flex-col gap-0.5">
                {navItems.map((item) => {
                  const Icon = item.icon;
                  return (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      onClick={closeMobileDrawer}
                      className={({ isActive }) => cn(
                        'group relative flex h-9 items-center gap-3 rounded-md px-3 text-[13px] font-medium transition',
                        isActive
                          ? 'bg-primary/10 text-primary'
                          : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                      )}
                    >
                      {({ isActive }) => (
                        <>
                          {isActive && (
                            <span className="absolute left-0 top-1/2 h-4 w-[2px] -translate-y-1/2 rounded-full bg-primary" />
                          )}
                          <Icon className="h-[17px] w-[17px] shrink-0" />
                          <span className="truncate">{item.label}</span>
                        </>
                      )}
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