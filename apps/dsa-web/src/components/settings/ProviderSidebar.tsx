import React from 'react';
import { Cpu, Globe, Sparkles, Radio } from 'lucide-react';
import { cn } from '../../utils/cn';

export interface ProviderInfo {
  key: string;
  title: string;
  icon: React.ReactNode;
  status: 'configured' | 'not_configured' | 'active';
}

interface ProviderSidebarProps {
  providers: ProviderInfo[];
  selectedKey: string;
  onSelect: (key: string) => void;
}

const PROVIDER_ICONS: Record<string, React.ReactNode> = {
  main: <Radio className="h-4 w-4" />,
  deepseek: <Cpu className="h-4 w-4" />,
  openai: <Globe className="h-4 w-4" />,
  gemini: <Sparkles className="h-4 w-4 text-amber-400" />,
  anthropic: <Cpu className="h-4 w-4 text-orange-400" />,
};

export const ProviderSidebar: React.FC<ProviderSidebarProps> = ({
  providers,
  selectedKey,
  onSelect,
}) => {
  return (
    <nav className="flex flex-col gap-1" aria-label="Provider navigation">
      {providers.map((provider) => {
        const icon = PROVIDER_ICONS[provider.key] ?? PROVIDER_ICONS.main;
        const isActive = selectedKey === provider.key;
        const isConfigured = provider.status === 'configured' || provider.status === 'active';

        return (
          <button
            key={provider.key}
            type="button"
            onClick={() => onSelect(provider.key)}
            className={cn(
              'flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm transition-all',
              isActive
                ? 'bg-cyan/10 text-foreground shadow-sm'
                : 'text-secondary-text hover:bg-hover/40 hover:text-foreground',
            )}
            aria-current={isActive ? 'page' : undefined}
          >
            <span
              className={cn(
                'flex h-7 w-7 shrink-0 items-center justify-center rounded-md border transition-colors',
                isActive
                  ? 'border-cyan/30 bg-cyan/10 text-cyan'
                  : 'border-border/40 bg-surface-1 text-secondary-text',
              )}
            >
              {icon}
            </span>
            <span className="min-w-0 flex-1 truncate font-medium">{provider.title}</span>
            <span
              className={cn(
                'h-2 w-2 shrink-0 rounded-full transition-colors',
                isConfigured ? 'bg-success' : 'bg-border/60',
              )}
              aria-label={isConfigured ? '已配置' : '未配置'}
            />
          </button>
        );
      })}
    </nav>
  );
};
