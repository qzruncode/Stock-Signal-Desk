import { useEffect, useState } from 'react';
import { ArrowLeft, Bell, Box, Hammer, MessageSquareText, Rss } from 'lucide-react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { Link, useSearchParams } from 'react-router-dom';
import { SettingsSidebar } from '../components/settings/SettingsSidebar';
import type { SettingsCategory } from '../components/settings/SettingsSidebar';
import { ModelSettingsView } from '../components/settings/ModelSettingsView';
import { AgentPromptView } from '../components/agentPrompts/AgentPromptView';
import { NotificationSettingsView } from '../components/settings/NotificationSettingsView';
import { MobileSettingsNavigation } from '../components/settings/MobileSettingsNavigation';
import { RssSettingsView } from '../components/settings/RssSettingsView';
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
    id: 'rss',
    label: 'RSS 数据源',
    icon: Rss,
    available: true,
    description: '浏览已筛选的财经来源并预览 Feed',
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
  const prefersReducedMotion = useReducedMotion();
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
  const handleCategorySelect = (id: string) => {
    setActiveId(id);
    setSearchParams({ tab: id }, { replace: true });
  };

  return (
    <motion.div
      className="mx-auto max-w-6xl space-y-6 py-6"
      initial={{ opacity: 0, y: prefersReducedMotion ? 0 : 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: prefersReducedMotion ? 0 : 0.26,
        ease: [0.22, 1, 0.36, 1],
      }}
    >
      <header>
        <div className="flex items-center justify-between lg:block">
          <Link
            to="/"
            viewTransition
            className="inline-flex items-center gap-1 text-sm text-secondary-text transition hover:text-foreground lg:mb-3"
          >
            <ArrowLeft className="h-4 w-4" />
            返回首页
          </Link>
          <MobileSettingsNavigation
            categories={SETTINGS_CATEGORIES}
            activeId={activeId}
            onSelect={handleCategorySelect}
          />
        </div>
        <h1 className="hidden text-2xl font-semibold text-foreground lg:block">AI 助手设置</h1>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[14rem_1fr]">
        <aside className="hidden lg:sticky lg:top-4 lg:block lg:self-start">
          <SettingsSidebar
            categories={SETTINGS_CATEGORIES}
            activeId={activeId}
            onSelect={handleCategorySelect}
          />
        </aside>

        <AnimatePresence mode="wait" initial={false}>
          <motion.main
            key={activeId}
            className="min-w-0"
            initial={{ opacity: 0, x: prefersReducedMotion ? 0 : 10 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: prefersReducedMotion ? 0 : -6 }}
            transition={{
              duration: prefersReducedMotion ? 0 : 0.2,
              ease: [0.22, 1, 0.36, 1],
            }}
          >
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
            ) : activeCategory?.id === 'rss' ? (
              <RssSettingsView />
            ) : null}
          </motion.main>
        </AnimatePresence>
      </div>
    </motion.div>
  );
};

export default SettingPage;
