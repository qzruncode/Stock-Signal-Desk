import { useEffect, useState } from 'react';
import { ArrowLeft, Bell, Box, Hammer, MessageSquareText } from 'lucide-react';
import { Link, useSearchParams } from 'react-router-dom';
import { SettingsSidebar } from '../components/settings/SettingsSidebar';
import type { SettingsCategory } from '../components/settings/SettingsSidebar';
import { ModelSettingsView } from '../components/settings/ModelSettingsView';
import { AgentPromptView } from '../components/agentPrompts/AgentPromptView';
import { NotificationSettingsView } from '../components/settings/NotificationSettingsView';
import { ToolRegistryView } from '../components/tools/ToolRegistryView';

const SETTINGS_CATEGORIES: SettingsCategory[] = [
  {
    id: 'tools',
    label: '助手工具',
    icon: Hammer,
    available: true,
    description: '查看和验证 AI 助手可调用的全部工具',
  },
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
  const [searchParams, setSearchParams] = useSearchParams();
  const [activeId, setActiveId] = useState<string>(() => {
    const requested = searchParams.get('tab');
    if (requested && SETTINGS_CATEGORIES.some((category) => category.id === requested && category.available)) {
      return requested;
    }
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
        <h1 className="text-2xl font-semibold text-foreground">AI 助手设置</h1>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[14rem_1fr]">
        <aside className="lg:sticky lg:top-4 lg:self-start">
          <SettingsSidebar
            categories={SETTINGS_CATEGORIES}
            activeId={activeId}
            onSelect={(id) => {
              setActiveId(id);
              setSearchParams({ tab: id }, { replace: true });
            }}
          />
        </aside>

        <main className="min-w-0">
          {activeCategory?.id === 'tools' ? (
            <section className="space-y-4">
              <header>
                <h2 className="text-lg font-semibold text-foreground">助手工具</h2>
                <p className="mt-1 text-sm text-secondary-text">
                  查看 AI 助手能够自动调用的实时数据、研究与操作工具，也可以展开单个工具验证结果。
                </p>
              </header>
              <ToolRegistryView />
            </section>
          ) : activeCategory?.id === 'model' ? (
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
