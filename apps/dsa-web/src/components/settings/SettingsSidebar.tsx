import type { LucideIcon } from 'lucide-react';
import { cn } from '../../utils/cn';

export interface SettingsCategory {
  id: string;
  label: string;
  icon: LucideIcon;
  available: boolean;
  description?: string;
}

interface SettingsSidebarProps {
  categories: SettingsCategory[];
  activeId: string;
  onSelect: (id: string) => void;
}

export const SettingsSidebar: React.FC<SettingsSidebarProps> = ({
  categories,
  activeId,
  onSelect,
}) => {
  return (
    <nav
      aria-label="设置分类导航"
      className="flex flex-col gap-1"
    >
      {categories.map((category) => {
        const Icon = category.icon;
        const isActive = category.id === activeId;
        const isDisabled = !category.available;
        return (
          <button
            key={category.id}
            type="button"
            onClick={() => {
              if (!isDisabled) onSelect(category.id);
            }}
            disabled={isDisabled}
            aria-current={isActive ? 'page' : undefined}
            title={isDisabled ? '尚未实现' : undefined}
            className={cn(
              'flex items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition',
              isDisabled && 'cursor-not-allowed opacity-50',
              !isDisabled && isActive && 'bg-primary/10 text-primary',
              !isDisabled && !isActive && 'text-secondary-text hover:bg-accent hover:text-foreground',
            )}
          >
            <span
              className={cn(
                'flex h-7 w-7 shrink-0 items-center justify-center rounded-md border',
                isActive
                  ? 'border-primary/30 bg-primary/10 text-primary'
                  : 'border-border/55 bg-elevated/75 text-muted-foreground',
              )}
            >
              <Icon className="h-4 w-4" />
            </span>
            <span className="min-w-0 flex-1 truncate font-medium">{category.label}</span>
            {isDisabled ? (
              <span className="rounded-sm bg-muted/40 px-1.5 py-0.5 text-[10px] text-muted-foreground">
                即将推出
              </span>
            ) : null}
          </button>
        );
      })}
    </nav>
  );
};

export default SettingsSidebar;