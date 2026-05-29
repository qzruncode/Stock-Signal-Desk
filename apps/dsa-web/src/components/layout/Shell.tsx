import type React from 'react';
import { useState } from 'react';
import { BarChart3, ChevronLeft, ChevronRight, GitBranch, Home, Search, Settings, Star } from 'lucide-react';
import { NavLink, Outlet } from 'react-router-dom';
import { cn } from '../../utils/cn';

type ShellProps = {
  children?: React.ReactNode;
};

export const Shell: React.FC<ShellProps> = ({ children }) => {
  const [collapsed, setCollapsed] = useState(false);

  const navItems = [
    { to: '/', label: '工作台', icon: Home },
    { to: '/stocks', label: '全市场股票', icon: Search },
    { to: '/portfolio', label: '管理自选股', icon: Star },
    { to: '/workflows', label: '工作流编排', icon: GitBranch },
    { to: '/settings', label: '模型 API 配置', icon: Settings },
  ];

  return (
    <div className="min-h-screen bg-background text-foreground selection:bg-primary/20">
      <div className="mx-auto flex min-h-screen w-full max-w-[1720px] gap-3 px-3 py-3 sm:px-4 sm:py-4 lg:px-5">
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

        <main className="min-h-0 min-w-0 flex-1 touch-pan-y">
          {children ?? <Outlet />}
        </main>
      </div>
    </div>
  );
};
