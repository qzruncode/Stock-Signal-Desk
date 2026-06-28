import React from 'react';
import { cn } from '../../utils/cn';

interface ViewTabsProps {
  activeView: 'summary' | 'details';
  onChange: (view: 'summary' | 'details') => void;
}

export const ViewTabs: React.FC<ViewTabsProps> = ({ activeView, onChange }) => (
  <div className="flex items-center gap-2 rounded-xl border border-subtle bg-surface p-1">
    <button
      type="button"
      onClick={() => onChange('summary')}
      className={cn(
        'h-8 rounded-lg px-3 text-sm transition-colors',
        activeView === 'summary'
          ? 'bg-primary/10 text-primary'
          : 'text-muted-text hover:bg-hover hover:text-foreground',
      )}
    >
      汇总 MD
    </button>
    <button
      type="button"
      onClick={() => onChange('details')}
      className={cn(
        'h-8 rounded-lg px-3 text-sm transition-colors',
        activeView === 'details'
          ? 'bg-primary/10 text-primary'
          : 'text-muted-text hover:bg-hover hover:text-foreground',
      )}
    >
      单股明细
    </button>
  </div>
);