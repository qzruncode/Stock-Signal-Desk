import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Bell, Hammer } from 'lucide-react';
import { describe, expect, it, vi } from 'vitest';
import { MobileSettingsNavigation } from './MobileSettingsNavigation';
import type { SettingsCategory } from './SettingsSidebar';

const categories: SettingsCategory[] = [
  {
    id: 'tools',
    label: '助手工具',
    icon: Hammer,
    available: true,
  },
  {
    id: 'notification',
    label: '通知设置',
    icon: Bell,
    available: true,
  },
];

describe('MobileSettingsNavigation', () => {
  it('opens the settings drawer and closes it after selecting a category', async () => {
    const onSelect = vi.fn();
    render(
      <MobileSettingsNavigation
        categories={categories}
        activeId="tools"
        onSelect={onSelect}
      />,
    );

    const trigger = screen.getByRole('button', { name: '打开设置菜单' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(trigger);

    expect(screen.getByRole('dialog', { name: '设置' })).toBeInTheDocument();
    expect(trigger).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(screen.getByRole('button', { name: '通知设置' }));

    expect(onSelect).toHaveBeenCalledWith('notification');
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '设置' })).not.toBeInTheDocument();
    });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });
});
