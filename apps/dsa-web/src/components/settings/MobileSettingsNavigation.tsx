import { Menu } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Drawer } from '../common';
import { Button } from '../ui/button';
import { SettingsSidebar } from './SettingsSidebar';
import type { SettingsCategory } from './SettingsSidebar';

interface MobileSettingsNavigationProps {
  categories: SettingsCategory[];
  activeId: string;
  onSelect: (id: string) => void;
}

export const MobileSettingsNavigation: React.FC<MobileSettingsNavigationProps> = ({
  categories,
  activeId,
  onSelect,
}) => {
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;

    const desktopQuery = window.matchMedia('(min-width: 1024px)');
    const closeOnDesktop = (event: MediaQueryListEvent) => {
      if (event.matches) setIsOpen(false);
    };

    desktopQuery.addEventListener('change', closeOnDesktop);
    return () => desktopQuery.removeEventListener('change', closeOnDesktop);
  }, []);

  const handleSelect = (id: string) => {
    onSelect(id);
    setIsOpen(false);
  };

  return (
    <div className="lg:hidden">
      <Button
        variant="ghost"
        size="icon"
        type="button"
        onClick={() => setIsOpen(true)}
        aria-label="打开设置菜单"
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        className="group h-10 w-10 rounded-lg text-secondary-text transition-all duration-200 hover:bg-hover hover:text-foreground active:scale-90"
      >
        <Menu className="h-5 w-5 transition-transform duration-200 group-hover:scale-110" />
      </Button>

      <Drawer
        isOpen={isOpen}
        onClose={() => setIsOpen(false)}
        title="设置"
        eyebrow={null}
        side="right"
        width="max-w-xs"
      >
        <SettingsSidebar
          categories={categories}
          activeId={activeId}
          onSelect={handleSelect}
        />
      </Drawer>
    </div>
  );
};

export default MobileSettingsNavigation;
