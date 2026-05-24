import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Bell,
  BellOff,
  FileText,
  History,
  Layers3,
  Menu,
  RadioTower,
  RefreshCw,
  Settings,
  SlidersHorizontal,
  Sparkles,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { analysisApi } from '../api/analysis';
import { systemConfigApi } from '../api/systemConfig';
import { promptsApi, type PromptTemplateItem } from '../api/prompts';
import { ApiErrorAlert, ConfirmDialog, Button, EmptyState, InlineAlert } from '../components/common';
import { DashboardStateBlock } from '../components/dashboard';
import { WatchlistPanel } from '../components/dashboard/WatchlistPanel';
import { BatchPanel } from '../components/batch';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { HistoryList } from '../components/history';
import { ReportMarkdown, ConversationReport } from '../components/report';
import { TemplateManager } from '../components/templates/TemplateManager';
import { TaskPanel } from '../components/tasks';
import { useDashboardLifecycle, useHomeDashboardState } from '../hooks';
import type { AnalysisReport, TaskInfo, TaskStatus } from '../types/analysis';
import type { SetupStatusResponse } from '../types/systemConfig';
import { getReportText, normalizeReportLanguage } from '../utils/reportLanguage';

const HomePage: React.FC = () => {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const dashboardScrollRef = useRef<HTMLElement | null>(null);

  // Prompt template state
  const [templates, setTemplates] = useState<PromptTemplateItem[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState('');

  const [templateManagerOpen, setTemplateManagerOpen] = useState(false);
  const [setupStatus, setSetupStatus] = useState<SetupStatusResponse | null>(null);
  const [selectedTaskStatus, setSelectedTaskStatus] = useState<TaskStatus | null>(null);
  const [isLoadingTaskStatus, setIsLoadingTaskStatus] = useState(false);

  const {
    query,
    inputError,
    duplicateError,
    error,
    isAnalyzing,
    historyItems,
    selectedHistoryIds,
    isDeletingHistory,
    isLoadingHistory,
    isLoadingMore,
    hasMore,
    selectedReport,
    isLoadingReport,
    activeTasks,
    pendingAutoSelectCode,
    markdownDrawerOpen,
    setQuery,
    clearError,
    loadInitialHistory,
    refreshHistory,
    loadMoreHistory,
    selectHistoryItem,
    toggleHistorySelection,
    toggleSelectAllVisible,
    deleteSelectedHistory,
    submitAnalysis,
    notify,
    setNotify,
    syncTaskCreated,
    syncTaskUpdated,
    syncTaskFailed,
    removeTask,
    setPendingAutoSelect,
    autoSelectByStockCode,
    openMarkdownDrawer,
    closeMarkdownDrawer,
    selectedIds,
  } = useHomeDashboardState();

  useEffect(() => {
    document.title = '每日选股分析 - DSA';
  }, []);

  useEffect(() => {
    let active = true;
    systemConfigApi.getSetupStatus()
      .then((status) => {
        if (active) {
          setSetupStatus(status);
        }
      })
      .catch(() => {
        if (active) {
          setSetupStatus(null);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  // Load prompt templates
  useEffect(() => {
    let active = true;
    promptsApi.getPromptTemplates().then((items) => {
      if (!active) return;
      setTemplates(items);
      if (items.length > 0 && !selectedTemplateId) {
        const defaultTemplate = items.find((t) => t.is_default);
        setSelectedTemplateId(defaultTemplate?.id || items[0]?.id || '');
      }
    }).catch(() => {});
    return () => { active = false; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const reportLanguage = normalizeReportLanguage(selectedReport?.meta.reportLanguage);
  const reportText = getReportText(reportLanguage);
  const setupNeedsAction = setupStatus ? !setupStatus.isComplete : false;
  const setupMissingLabels = useMemo(() => {
    if (!setupStatus) {
      return '';
    }
    const requiredNeedsAction = setupStatus.checks
      .filter((check) => check.required && check.status === 'needs_action')
      .map((check) => check.title);
    return requiredNeedsAction.slice(0, 3).join('、');
  }, [setupStatus]);

  useDashboardLifecycle({
    loadInitialHistory,
    refreshHistory,
    syncTaskCreated,
    syncTaskUpdated,
    syncTaskFailed,
    removeTask,
    onTaskAutoSelect: (task) => {
      setSelectedTaskStatus((current) => (
        current?.taskId === task.taskId
          ? {
              taskId: task.taskId,
              status: task.status,
              progress: task.progress,
              error: task.error,
              stockName: task.stockName,
              originalQuery: task.originalQuery,
              selectionSource: task.selectionSource,
              promptTemplateId: task.promptTemplateId,
              promptTemplateName: task.promptTemplateName,
              conversation: task.conversation,
            }
          : current
      ));
      void autoSelectByStockCode(task.stockCode);
    },
  });

  const taskPreviewReport = useMemo<AnalysisReport | null>(() => {
    if (!selectedTaskStatus) {
      return null;
    }

    if (selectedTaskStatus.result?.report) {
      return selectedTaskStatus.result.report;
    }

    const activeTask = activeTasks.find((task) => task.taskId === selectedTaskStatus.taskId);
    const conversation = activeTask?.conversation || selectedTaskStatus.conversation;
    if (!conversation) {
      return null;
    }

    const response = conversation.response || '';
    return {
      meta: {
        queryId: selectedTaskStatus.taskId,
        stockCode: activeTask?.stockCode || '',
        stockName: selectedTaskStatus.stockName || activeTask?.stockName || '',
        reportType: 'conversation',
        reportLanguage: 'zh',
        createdAt: new Date().toISOString(),
        modelUsed: conversation.modelUsed,
      },
      summary: {
        analysisSummary: response || 'AI 正在生成输出',
        operationAdvice: '',
        trendPrediction: '',
        sentimentScore: 50,
      },
      conversation,
    };
  }, [activeTasks, selectedTaskStatus]);

  const handleHistoryItemClick = useCallback((recordId: number) => {
    setSelectedTaskStatus(null);
    setPendingAutoSelect(null);
    void selectHistoryItem(recordId);
    setSidebarOpen(false);
  }, [selectHistoryItem, setPendingAutoSelect]);

  const handleTaskClick = useCallback((task: TaskInfo) => {
    setPendingAutoSelect(task.stockCode);
    setSelectedTaskStatus({
      taskId: task.taskId,
      status: task.status,
      progress: task.progress,
      error: task.error,
      stockName: task.stockName,
      originalQuery: task.originalQuery,
      selectionSource: task.selectionSource,
      promptTemplateId: task.promptTemplateId,
      promptTemplateName: task.promptTemplateName,
      conversation: task.conversation,
    });
    setIsLoadingTaskStatus(true);
    void analysisApi.getStatus(task.taskId)
      .then((status) => {
        setSelectedTaskStatus(status);
        if (status.result?.report || status.conversation?.response) {
          setPendingAutoSelect(null);
        }
      })
      .catch((err) => {
        setPendingAutoSelect(null);
        console.warn('加载任务对话失败:', err);
      })
      .finally(() => {
        setIsLoadingTaskStatus(false);
      });
    dashboardScrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
    setSidebarOpen(false);
  }, [setPendingAutoSelect]);

  const handleSubmitAnalysis = useCallback(
    (
      stockCode?: string,
      stockName?: string,
      selectionSource?: 'manual' | 'autocomplete' | 'import',
    ) => {
      void submitAnalysis({
        stockCode,
        stockName,
        originalQuery: query,
        selectionSource: selectionSource ?? 'manual',
        promptTemplateId: selectedTemplateId || undefined,
      });
    },
    [query, submitAnalysis, selectedTemplateId],
  );

  const handleReanalyze = useCallback(() => {
    if (!selectedReport) {
      return;
    }

    void submitAnalysis({
      stockCode: selectedReport.meta.stockCode,
      stockName: selectedReport.meta.stockName,
      originalQuery: selectedReport.meta.stockCode,
      selectionSource: 'manual',
      forceRefresh: true,
      promptTemplateId: selectedTemplateId || undefined,
    });
  }, [selectedReport, submitAnalysis, selectedTemplateId]);

  const handleSelectStock = useCallback(
    (code: string) => {
      setQuery(code);
      dashboardScrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
    },
    [setQuery],
  );

  const handleDeleteSelectedHistory = useCallback(() => {
    void deleteSelectedHistory();
    setShowDeleteConfirm(false);
  }, [deleteSelectedHistory]);

  const selectedTemplate = useMemo(
    () => templates.find((template) => template.id === selectedTemplateId),
    [selectedTemplateId, templates],
  );

  const sidebarContent = useMemo(
    () => (
      <div className="flex min-h-0 h-full flex-col gap-3 overflow-hidden">
        <TaskPanel
          tasks={activeTasks}
          onTaskClick={handleTaskClick}
        />
        <HistoryList
          items={historyItems}
          isLoading={isLoadingHistory}
          isLoadingMore={isLoadingMore}
          hasMore={hasMore}
          selectedId={selectedReport?.meta.id}
          selectedIds={selectedIds}
          isDeleting={isDeletingHistory}
          onItemClick={handleHistoryItemClick}
          onLoadMore={() => void loadMoreHistory()}
          onToggleItemSelection={toggleHistorySelection}
          onToggleSelectAll={toggleSelectAllVisible}
          onDeleteSelected={() => setShowDeleteConfirm(true)}
          className="flex-1 overflow-hidden"
        />
      </div>
    ),
    [
      activeTasks,
      hasMore,
      handleTaskClick,
      historyItems,
      isDeletingHistory,
      isLoadingHistory,
      isLoadingMore,
      handleHistoryItemClick,
      loadMoreHistory,
      selectedIds,
      selectedReport?.meta.id,
      toggleHistorySelection,
      toggleSelectAllVisible,
    ],
  );

  return (
    <div
      data-testid="home-dashboard"
      className="relative flex min-h-[calc(100vh-1.5rem)] w-full overflow-hidden rounded-[1.35rem] border border-[#d6dee8] bg-[#f7f9fc] shadow-[0_18px_60px_rgba(24,45,77,0.12)] sm:min-h-[calc(100vh-2rem)]"
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-40 bg-[linear-gradient(90deg,rgba(8,145,178,0.16),rgba(16,185,129,0.12),rgba(245,158,11,0.10))]" />
      <div className="relative grid min-h-0 w-full grid-cols-1 gap-0 lg:grid-cols-[23rem_minmax(0,1fr)] 2xl:grid-cols-[23rem_minmax(0,1fr)_24rem]">
        <aside className="hidden min-h-0 border-r border-[#dbe3ed] bg-white/82 p-4 backdrop-blur-xl lg:flex lg:flex-col">
          <div className="mb-4">
            <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Signal Desk</p>
            <h1 className="mt-1 text-2xl font-semibold text-slate-950">选股通知工作台</h1>
            <p className="mt-2 text-sm text-slate-500">搜索股票，选择模板，追踪 AI 完整输入与输出。</p>
          </div>

          <div className="rounded-2xl border border-[#dce5ef] bg-[#f8fbff] p-3 shadow-[0_8px_24px_rgba(15,23,42,0.06)]">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-600">
                <RadioTower className="h-3.5 w-3.5 text-cyan-600" />
                实时任务
              </span>
              <button
                type="button"
                onClick={() => setTemplateManagerOpen(true)}
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-[#d8e1ec] bg-white text-slate-500 transition hover:border-cyan-300 hover:text-cyan-700"
                title="管理分析模板"
                aria-label="管理分析模板"
              >
                <SlidersHorizontal className="h-4 w-4" />
              </button>
            </div>

            <StockAutocomplete
              value={query}
              onChange={setQuery}
              onSubmit={(stockCode, stockName, selectionSource) => {
                handleSubmitAnalysis(stockCode, stockName, selectionSource);
              }}
              placeholder="输入股票代码或名称，如 600519、贵州茅台、AAPL"
              disabled={isAnalyzing}
              className={inputError ? 'border-danger/50' : undefined}
            />

            <div className="mt-3 grid grid-cols-[1fr_auto] gap-2">
              {templates.length > 0 ? (
                <select
                  value={selectedTemplateId}
                  onChange={(e) => setSelectedTemplateId(e.target.value)}
                  className="h-10 min-w-0 rounded-xl border border-[#d8e1ec] bg-white px-3 text-sm text-slate-700 outline-none transition hover:border-cyan-300 focus:border-cyan-500 focus:ring-4 focus:ring-cyan-100"
                  aria-label="分析模板"
                >
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              ) : (
                <div className="flex h-10 items-center rounded-xl border border-[#d8e1ec] bg-white px-3 text-sm text-slate-400">加载模板</div>
              )}
              <label className="inline-flex h-10 cursor-pointer items-center gap-2 rounded-xl border border-[#d8e1ec] bg-white px-3 text-xs font-medium text-slate-600 transition hover:border-cyan-300">
                <input
                  type="checkbox"
                  checked={notify}
                  onChange={(e) => setNotify(e.target.checked)}
                  className="sr-only"
                />
                {notify ? <Bell className="h-4 w-4 text-emerald-600" /> : <BellOff className="h-4 w-4 text-slate-400" />}
                推送通知
              </label>
            </div>

            <button
              type="button"
              onClick={() => handleSubmitAnalysis()}
              disabled={!query || isAnalyzing}
              className="mt-3 flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-slate-950 px-4 text-sm font-semibold text-white shadow-[0_10px_24px_rgba(15,23,42,0.22)] transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none"
            >
              {isAnalyzing ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
              {isAnalyzing ? '分析中' : '分析'}
            </button>

            <div className="mt-3 rounded-xl border border-dashed border-[#d3dde9] bg-white/70 px-3 py-2 text-xs text-slate-500">
              当前模板：<span className="font-medium text-slate-700">{selectedTemplate?.name || '默认模板'}</span>
            </div>
          </div>

          <div className="mt-3 flex min-h-0 flex-1 flex-col">
            {sidebarContent}
          </div>
        </aside>

        {sidebarOpen ? (
          <div className="fixed inset-0 z-40 lg:hidden" onClick={() => setSidebarOpen(false)}>
            <div className="page-drawer-overlay absolute inset-0" />
            <div
              className="dashboard-card absolute bottom-0 left-0 top-0 flex w-[min(21rem,86vw)] flex-col overflow-hidden !rounded-none !rounded-r-2xl p-3 shadow-2xl"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-text">Signal Desk</p>
                  <h2 className="text-base font-semibold text-foreground">历史记录</h2>
                </div>
                <button
                  type="button"
                  onClick={() => setSidebarOpen(false)}
                  className="rounded-lg px-2 py-1 text-sm text-secondary-text hover:bg-hover"
                >
                  关闭
                </button>
              </div>
              {sidebarContent}
            </div>
          </div>
        ) : null}

        <main className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          <header className="flex flex-shrink-0 items-center gap-3 border-b border-[#dbe3ed] bg-white/72 px-3 py-3 backdrop-blur-xl sm:px-5">
            <button
              onClick={() => setSidebarOpen(true)}
              className="inline-flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl border border-[#d8e1ec] bg-white text-slate-600 shadow-sm transition hover:border-cyan-300 hover:text-cyan-700 lg:hidden"
              aria-label="历史记录"
            >
              <Menu className="h-5 w-5" />
            </button>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Daily Stock Analysis</p>
              <h2 className="truncate text-lg font-semibold text-slate-950 sm:text-xl">选股通知工作台</h2>
            </div>
            <div className="hidden items-center gap-2 text-xs text-slate-500 sm:flex">
              <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-emerald-700">
                <Bell className="h-3.5 w-3.5" />
                推送通知
              </span>
              <span className="inline-flex items-center gap-1 rounded-full border border-cyan-200 bg-cyan-50 px-3 py-1 text-cyan-700">
                <FileText className="h-3.5 w-3.5" />
                AI 输出可追踪
              </span>
              <Link
                to="/settings"
                className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-[#d8e1ec] bg-white text-slate-500 shadow-sm transition hover:border-cyan-300 hover:text-cyan-700"
                aria-label="模型 API 配置"
                title="模型 API 配置"
              >
                <Settings className="h-4 w-4" />
              </Link>
            </div>
          </header>

          <div className="border-b border-[#dbe3ed] bg-white/64 px-3 py-3 lg:hidden">
            <div className="flex min-w-0 gap-2">
              <StockAutocomplete
                value={query}
                onChange={setQuery}
                onSubmit={(stockCode, stockName, selectionSource) => {
                  handleSubmitAnalysis(stockCode, stockName, selectionSource);
                }}
                placeholder="输入股票代码或名称，如 600519、贵州茅台、AAPL"
                disabled={isAnalyzing}
                className={inputError ? 'border-danger/50' : undefined}
              />
              <button
                type="button"
                onClick={() => handleSubmitAnalysis()}
                disabled={!query || isAnalyzing}
                className="h-11 rounded-xl bg-slate-950 px-4 text-sm font-semibold text-white disabled:bg-slate-300"
              >
                分析
              </button>
            </div>
          </div>

          {(inputError || duplicateError || setupNeedsAction) ? (
            <div className="space-y-2 border-b border-[#dbe3ed] bg-white/58 px-3 py-3 sm:px-5">
              {inputError ? (
                <InlineAlert
                  variant="danger"
                  title="输入有误"
                  message={inputError}
                  className="rounded-xl px-3 py-2 text-xs shadow-none"
                />
              ) : null}
              {!inputError && duplicateError ? (
                <InlineAlert
                  variant="warning"
                  title="任务已存在"
                  message={duplicateError}
                  className="rounded-xl px-3 py-2 text-xs shadow-none"
                />
              ) : null}
              {setupNeedsAction ? (
                <InlineAlert
                  variant="warning"
                  title="基础配置未完成"
                  message={
                    setupMissingLabels
                      ? `还缺少 ${setupMissingLabels}。请在服务端配置中补齐后刷新页面。`
                      : '还缺少基础配置。请在服务端配置中补齐后刷新页面。'
                  }
                  className="rounded-xl px-3 py-2 text-xs shadow-none"
                />
              ) : null}
            </div>
          ) : null}

          <section
            ref={dashboardScrollRef}
            data-testid="home-dashboard-scroll"
            className="min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-5"
          >
            <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-4">
              <div className="grid gap-4 2xl:hidden xl:grid-cols-2">
                <WatchlistPanel onSelectStock={handleSelectStock} />
                <BatchPanel
                  templates={templates}
                  selectedTemplateId={selectedTemplateId}
                  onTemplateChange={setSelectedTemplateId}
                />
              </div>

              <div className="rounded-2xl border border-[#dbe5ef] bg-white/88 p-4 shadow-[0_12px_34px_rgba(15,23,42,0.08)]">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">
                      <Layers3 className="h-3.5 w-3.5" />
                      Analysis Canvas
                    </p>
                    <h3 className="mt-1 text-lg font-semibold text-slate-950">AI 对话与报告</h3>
                  </div>
                  {selectedReport ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <Button
                        variant="home-action-ai"
                        size="sm"
                        disabled={isAnalyzing || selectedReport.meta.id === undefined}
                        onClick={handleReanalyze}
                      >
                        <RefreshCw className="h-4 w-4" />
                        {reportText.reanalyze}
                      </Button>
                      <Button
                        variant="home-action-ai"
                        size="sm"
                        disabled={selectedReport.meta.id === undefined}
                        onClick={openMarkdownDrawer}
                      >
                        <FileText className="h-4 w-4" />
                        {reportText.fullReport}
                      </Button>
                    </div>
                  ) : null}
                </div>
              </div>

            {error ? (
              <ApiErrorAlert
                error={error}
                className="mb-3"
                onDismiss={clearError}
              />
            ) : null}
            {isLoadingTaskStatus ? (
              <div className="flex min-h-[24rem] flex-col items-center justify-center rounded-2xl border border-[#dbe5ef] bg-white/82">
                <DashboardStateBlock title="加载任务对话中..." loading />
              </div>
            ) : taskPreviewReport ? (
              <div className="space-y-4 pb-8">
                <ConversationReport data={taskPreviewReport} isHistory />
              </div>
            ) : isLoadingReport || pendingAutoSelectCode ? (
              <div className="flex min-h-[24rem] flex-col items-center justify-center rounded-2xl border border-[#dbe5ef] bg-white/82">
                <DashboardStateBlock
                  title={pendingAutoSelectCode ? `正在为 ${pendingAutoSelectCode} 生成分析报告...` : '加载报告中...'}
                  loading
                />
              </div>
            ) : selectedReport ? (
              <div className="space-y-4 pb-8">
                <ConversationReport data={selectedReport} isHistory />
              </div>
            ) : (
              <div className="flex min-h-[26rem] items-center justify-center rounded-2xl border border-dashed border-[#cbd8e6] bg-white/70">
                <EmptyState
                  title="开始分析"
                  description="输入股票代码进行分析，或从任务控制台选择历史报告查看。"
                  className="max-w-xl border-dashed"
                  icon={(
                    <Sparkles className="h-6 w-6" />
                  )}
                />
              </div>
            )}
            </div>
          </section>

        </main>

        <aside className="hidden min-h-0 border-l border-[#dbe3ed] bg-[#fbfcfe]/86 p-4 backdrop-blur-xl 2xl:flex 2xl:flex-col 2xl:gap-4">
          <WatchlistPanel onSelectStock={handleSelectStock} />
          <BatchPanel
            templates={templates}
            selectedTemplateId={selectedTemplateId}
            onTemplateChange={setSelectedTemplateId}
          />
          <div className="rounded-2xl border border-[#dbe5ef] bg-white/84 p-4 text-sm text-slate-500 shadow-[0_10px_28px_rgba(15,23,42,0.06)]">
            <div className="mb-2 flex items-center gap-2 font-medium text-slate-800">
              <History className="h-4 w-4 text-amber-600" />
              工作流
            </div>
            <div className="space-y-2 text-xs leading-5">
              <p>1. 选择模板，发起实时分析。</p>
              <p>2. 点击左侧任务查看完整 prompt 与 AI 回吐。</p>
              <p>3. 结果写入历史后可继续通知与复盘。</p>
            </div>
          </div>
        </aside>
      </div>

      {markdownDrawerOpen && selectedReport?.meta.id ? (
        <ReportMarkdown
          recordId={selectedReport.meta.id}
          stockName={selectedReport.meta.stockName || ''}
          stockCode={selectedReport.meta.stockCode}
          reportLanguage={reportLanguage}
          onClose={closeMarkdownDrawer}
        />
      ) : null}

      <ConfirmDialog
        isOpen={showDeleteConfirm}
        title="删除历史记录"
        message={
          selectedHistoryIds.length === 1
            ? '确认删除这条历史记录吗？删除后将不可恢复。'
            : `确认删除选中的 ${selectedHistoryIds.length} 条历史记录吗？删除后将不可恢复。`
        }
        confirmText={isDeletingHistory ? '删除中...' : '确认删除'}
        cancelText="取消"
        isDanger={true}
        onConfirm={handleDeleteSelectedHistory}
        onCancel={() => setShowDeleteConfirm(false)}
      />

      {templateManagerOpen && (
        <TemplateManager
          templates={templates}
          onTemplatesChange={setTemplates}
          selectedTemplateId={selectedTemplateId}
          onSelectTemplate={setSelectedTemplateId}
          onClose={() => setTemplateManagerOpen(false)}
        />
      )}
    </div>
  );
};

export default HomePage;
