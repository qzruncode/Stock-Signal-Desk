import { useEffect, useState } from 'react';
import { ArrowLeft, Bell, Box, MessageSquareText } from 'lucide-react';
import { Link } from 'react-router-dom';
import { SettingsSidebar } from '../components/settings/SettingsSidebar';
import type { SettingsCategory } from '../components/settings/SettingsSidebar';
import { ModelSettingsView } from '../components/settings/ModelSettingsView';
import { AgentPromptView } from '../components/agentPrompts/AgentPromptView';
import { NotificationSettingsView } from '../components/settings/NotificationSettingsView';

const SETTINGS_CATEGORIES: SettingsCategory[] = [
  {
    id: 'notification',
    label: '通知设置',
    icon: Bell,
    available: true,
    description: '企业微信渠道与发送测试',
  },
  {
    id: 'model',
    label: '模型设置',
    icon: Box,
    available: true,
    description: 'Anthropic / Claude Code 模型参数',
  },
  {
    id: 'prompt',
    label: 'AI 助手 Prompt',
    icon: MessageSquareText,
    available: true,
    description: '配置 AI 助手的系统提示词',
  },
];

const SettingPage: React.FC = () => {
  const [activeId, setActiveId] = useState<string>(() => {
    const firstAvailable = SETTINGS_CATEGORIES.find((category) => category.available);
    return firstAvailable?.id ?? SETTINGS_CATEGORIES[0].id;
  });

  useEffect(() => {
    document.title = '设置 - Stock Assistant';
  }, []);

  const activeCategory = SETTINGS_CATEGORIES.find((category) => category.id === activeId);

  return (
    <div className="mx-auto max-w-6xl space-y-6 py-6">
      <header>
        <Link
          to="/"
          className="mb-3 inline-flex items-center gap-1 text-sm text-secondary-text transition hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          返回首页
        </Link>
        <h1 className="text-2xl font-semibold text-foreground">设置</h1>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[14rem_1fr]">
        <aside className="lg:sticky lg:top-4 lg:self-start">
          <SettingsSidebar
            categories={SETTINGS_CATEGORIES}
            activeId={activeId}
            onSelect={setActiveId}
          />
        </aside>

        <main className="min-w-0">
          {activeCategory?.id === 'model' ? (
            <ModelSettingsView />
          ) : activeCategory?.id === 'prompt' ? (
            <AgentPromptView />
          ) : activeCategory?.id === 'notification' ? (
            <NotificationSettingsView />
          ) : null}
        </main>
      </div>
    </div>
  );
};

export default SettingPage;
