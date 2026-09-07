import { lazy, useEffect } from 'react';
import { ArrowLeft, Bell, Box, Database, Hammer, ListChecks, MessageSquareText, Monitor } from 'lucide-react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { Link, Navigate, useSearchParams } from 'react-router-dom';
import { SettingsSidebar } from '../components/settings/SettingsSidebar';
import type { SettingsCategory } from '../components/settings/SettingsSidebar';
import { MobileSettingsNavigation } from '../components/settings/MobileSettingsNavigation';
import { Button } from '../components/ui/button';

const RunExplorerSettingsView = lazy(() => import('./RunExplorerPage'));
const AgentMonitoringSettingsView = lazy(() => import('./AgentMonitoringPage'));
const ToolRegistryView = lazy(() => import('../components/tools/ToolRegistryView'));
const ModelSettingsView = lazy(() => import('../components/settings/ModelSettingsView'));
const AgentPromptView = lazy(() => import('../components/agentPrompts/AgentPromptView'));
const NotificationSettingsView = lazy(() => import('../components/settings/NotificationSettingsView'));
const DataMaintenanceSettingsView = lazy(() => import('../components/settings/DataMaintenanceSettingsView'));

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
    id: 'monitoring',
    label: '系统运行监控',
    icon: Monitor,
    available: true,
    description: '查看整体运行状态、告警与工具健康度',
  },
  {
    id: 'notification',
    label: '通知设置',
    icon: Bell,
    available: true,
    description: '企业微信渠道与发送测试',
  },
  {
    id: 'data-maintenance',
    label: '数据维护中心',
    icon: Database,
    available: true,
    description: '同步股票、K 线和财务基础数据',
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

  useEffect(() => {
    document.title = '设置 - Stock Assistant';
  }, []);

  const requestedTab = searchParams.get('tab');
  const firstAvailable = SETTINGS_CATEGORIES.find((category) => category.available);
  const activeId = requestedTab && SETTINGS_CATEGORIES.some((category) => category.id === requestedTab && category.available)
    ? requestedTab
    : firstAvailable?.id ?? SETTINGS_CATEGORIES[0].id;

  const activeCategory = SETTINGS_CATEGORIES.find((category) => category.id === activeId);
  const businessRoutes: Record<string, string> = { stocks: '/stocks', 'indicator-screening': '/screening', rss: '/sources' };
  const handleCategorySelect = (id: string) => {
    setSearchParams({ tab: id }, { replace: true });
  };

  if (requestedTab && businessRoutes[requestedTab]) return <Navigate to={businessRoutes[requestedTab]} replace />;

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
              <ToolRegistryView />
            ) : activeCategory?.id === 'runs' ? (
              <RunExplorerSettingsView embedded />
            ) : activeCategory?.id === 'monitoring' ? (
              <AgentMonitoringSettingsView embedded />
            ) : activeCategory?.id === 'model' ? (
              <ModelSettingsView />
            ) : activeCategory?.id === 'prompt' ? (
              <AgentPromptView />
            ) : activeCategory?.id === 'notification' ? (
              <NotificationSettingsView />
            ) : activeCategory?.id === 'data-maintenance' ? (
              <DataMaintenanceSettingsView />
            ) : null}
          </motion.main>
        </AnimatePresence>
      </div>
    </motion.div>
  );
};

export default SettingPage;
