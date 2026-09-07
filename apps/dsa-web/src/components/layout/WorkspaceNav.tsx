import { NavLink } from 'react-router-dom';
import { cn } from '../../utils/cn';

const entries = [
  ['/', '聊天'],
  ['/research', '研究档案'],
  ['/stocks', '自选股'],
  ['/screening', '指标选股'],
  ['/sources', '财经来源'],
  ['/runs', '运行记录'],
  ['/monitoring', '监控'],
  ['/setting', '设置'],
];

export function WorkspaceNav() {
  return (
    <nav
      aria-label="工作区导航"
      className="flex shrink-0 gap-1 overflow-x-auto border-b border-border px-4 py-2 text-sm"
    >
      {entries.map(([to, label]) => (
        <NavLink
          key={to}
          to={to}
          end
          className={({ isActive }) =>
            cn('whitespace-nowrap rounded px-3 py-2 hover:bg-muted', isActive && 'bg-muted font-medium text-primary')
          }
        >
          {label}
        </NavLink>
      ))}
    </nav>
  );
}
