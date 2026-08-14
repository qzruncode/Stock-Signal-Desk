import { lazy, useEffect, useState } from 'react';
import { Activity, ArrowLeft, Bell, Box, Database, Hammer, ListChecks, ListFilter, MessageSquareText, Rss } from 'lucide-react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { Link, useSearchParams } from 'react-router-dom';
import { SettingsSidebar } from '../components/settings/SettingsSidebar';
import type { SettingsCategory } from '../components/settings/SettingsSidebar';
import { MobileSettingsNavigation } from '../components/settings/MobileSettingsNavigation';
import { Button } from '../components/ui/button';
import { cn } from '../utils/cn';

const RunExplorerSettingsView = lazy(() => import('./RunExplorerPage'));
const ToolRegistryView = lazy(() => import('../components/tools/ToolRegistryView'));
const ModelSettingsView = lazy(() => import('../components/settings/ModelSettingsView'));
const AgentPromptView = lazy(() => import('../components/agentPrompts/AgentPromptView'));
const NotificationSettingsView = lazy(() => import('../components/settings/NotificationSettingsView'));
const RssSettingsView = lazy(() => import('../components/settings/RssSettingsView'));
const StockListSettingsView = lazy(() => import('../components/settings/StockListSettingsView'));
const DataMaintenanceSettingsView = lazy(() => import('../components/settings/DataMaintenanceSettingsView'));
const IndicatorScreeningSettingsView = lazy(() => import('../components/settings/IndicatorScreeningSettingsView'));

const SETTINGS_CATEGORIES: SettingsCategory[] = [
  {
    id: 'tools',
    label: '助手工具',
    icon: Hammer,
    available: true,
    description: '查看和验证 AI 助手可调用的全部工具',
  },
  {
    id: 'runs',
    label: '运行记录',
    icon: ListChecks,
    available: true,
    description: '查看工具调用、证据关联与运行质量',
  },
  {
    id: 'notification',
    label: '通知设置',
    icon: Bell,
    available: true,
    description: '企业微信渠道与发送测试',
  },
  {
    id: 'stocks',
    label: '股票列表',
    icon: ListFilter,
    available: true,
    description: '浏览全市场股票并管理自选分组',
  },
  {
    id: 'indicator-screening',
    label: '指标选股',
    icon: Activity,
    available: true,
    description: '使用技术指标筛选股票并保存到自选分组',
  },
  {
    id: 'data-maintenance',
    label: '数据维护中心',
    icon: Database,
    available: true,
    description: '同步股票、K 线和财务基础数据',
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
  const isTallCategory = activeCategory?.id === 'stocks' || activeCategory?.id === 'indicator-screening';
  const handleCategorySelect = (id: string) => {
    setActiveId(id);
    setSearchParams({ tab: id }, { replace: true });
  };

  return (
    <motion.div
      className={cn(
        'mx-auto max-w-6xl py-6',
        isTallCategory
          ? 'flex h-full min-h-0 flex-col space-y-3 overflow-hidden'
          : 'space-y-6',
      )}
      initial={{ opacity: 0, y: prefersReducedMotion ? 0 : 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: prefersReducedMotion ? 0 : 0.26,
        ease: [0.22, 1, 0.36, 1],
      }}
    >
      <header>
        <div className="relative flex items-center justify-between lg:block">
          <Button asChild variant="ghost" size="sm" className="h-7 gap-1 px-0 text-sm text-secondary-text hover:bg-transparent hover:text-foreground lg:mb-3">
            <Link to="/" viewTransition>
              <ArrowLeft className="h-4 w-4" />
              返回首页
            </Link>
          </Button>
          <span className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 whitespace-nowrap text-base font-semibold text-foreground lg:hidden">
            {activeCategory?.label}
          </span>
          <MobileSettingsNavigation
            categories={SETTINGS_CATEGORIES}
            activeId={activeId}
            onSelect={handleCategorySelect}
          />
        </div>
        <h1 className="hidden text-2xl font-semibold text-foreground lg:block">设置</h1>
      </header>

      <div className={cn(
        'grid grid-cols-1 gap-6 lg:grid-cols-[14rem_1fr]',
        isTallCategory && 'min-h-0 flex-1',
      )}>
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
            className={cn(
              'min-w-0',
              isTallCategory && 'min-h-0 overflow-hidden',
            )}
            initial={{ opacity: 0, x: prefersReducedMotion ? 0 : 10 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: prefersReducedMotion ? 0 : -6 }}
            transition={{
              duration: prefersReducedMotion ? 0 : 0.2,
              ease: [0.22, 1, 0.36, 1],
            }}
          >
            {activeCategory?.id === 'tools' ? (
              <ToolRegistryView />
            ) : activeCategory?.id === 'runs' ? (
              <RunExplorerSettingsView embedded />
            ) : activeCategory?.id === 'model' ? (
              <ModelSettingsView />
            ) : activeCategory?.id === 'prompt' ? (
              <AgentPromptView />
            ) : activeCategory?.id === 'notification' ? (
              <NotificationSettingsView />
            ) : activeCategory?.id === 'stocks' ? (
              <StockListSettingsView />
            ) : activeCategory?.id === 'indicator-screening' ? (
              <IndicatorScreeningSettingsView />
            ) : activeCategory?.id === 'data-maintenance' ? (
              <DataMaintenanceSettingsView />
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
