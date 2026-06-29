import type React from 'react';
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Bell,
  FileText,
  Menu,
} from 'lucide-react';
import { ConfirmDialog, InlineAlert } from '../components/common';
import { BatchPanel } from '../components/batch';
import { HistoryList } from '../components/history';
import HomeSidebar from '../components/home/HomeSidebar';
import { TemplateManager } from '../components/templates/TemplateManager';
import { TaskPanel } from '../components/tasks';
import {
  useDashboardLifecycle,
  useHomeDashboardState,
  usePromptTemplates,
  useSetupStatus,
  useTaskStatusPreview,
} from '../hooks';
import type { TaskInfo } from '../types/analysis';
import { getReportText, normalizeReportLanguage } from '../utils/reportLanguage';

const HomeAnalysisCanvas = lazy(() => import('../components/home/HomeAnalysisCanvas'));
const ReportMarkdown = lazy(() => import('../components/report/ReportMarkdown'));

const HomePage: React.FC = () => {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [templateManagerOpen, setTemplateManagerOpen] = useState(false);
  const dashboardScrollRef = useRef<HTMLElement | null>(null);

  const {
    templates,
    selectedTemplateId,
    setSelectedTemplateId,
    setTemplates,
    selectedTemplate,
  } = usePromptTemplates();
  const { setupNeedsAction, setupMissingLabels } = useSetupStatus();

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

  const {
    isLoadingTaskStatus,
    taskPreviewReport,
    selectTask,
    clearSelectedTaskStatus,
    updateSelectedTaskStatusFromTask,
  } = useTaskStatusPreview(activeTasks);

  useEffect(() => {
    document.title = 'Stock Assistant';
  }, []);

  const reportLanguage = normalizeReportLanguage(selectedReport?.meta.reportLanguage);
  const reportText = getReportText(reportLanguage);

  useDashboardLifecycle({
    loadInitialHistory,
    refreshHistory,
    syncTaskCreated,
    syncTaskUpdated,
    syncTaskFailed,
    removeTask,
    onTaskAutoSelect: (task: TaskInfo) => {
      updateSelectedTaskStatusFromTask(task);
      void autoSelectByStockCode(task.stockCode);
    },
  });

  const handleHistoryItemClick = useCallback((recordId: number) => {
    clearSelectedTaskStatus();
    setPendingAutoSelect(null);
    void selectHistoryItem(recordId);
    setSidebarOpen(false);
  }, [selectHistoryItem, setPendingAutoSelect, clearSelectedTaskStatus]);

  const handleTaskClick = useCallback((task: TaskInfo) => {
    selectTask(
      task,
      setPendingAutoSelect,
      () => setPendingAutoSelect(null),
      dashboardScrollRef.current,
    );
    setSidebarOpen(false);
  }, [selectTask, setPendingAutoSelect]);

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

  const handleDeleteSelectedHistory = useCallback(() => {
    void deleteSelectedHistory();
    setShowDeleteConfirm(false);
  }, [deleteSelectedHistory]);

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
      className="relative flex min-h-[calc(100vh-1.5rem)] w-full flex-1 overflow-hidden rounded-xl border border-slate-200 bg-slate-50 shadow-sm sm:min-h-[calc(100vh-2rem)]"
    >
      <div className="relative grid min-h-0 w-full grid-cols-1 gap-0 lg:grid-cols-[23rem_minmax(0,1fr)]">
        <HomeSidebar
          query={query}
          onQueryChange={setQuery}
          onSubmitAnalysis={handleSubmitAnalysis}
          isAnalyzing={isAnalyzing}
          inputError={inputError}
          templates={templates}
          selectedTemplateId={selectedTemplateId}
          onTemplateChange={setSelectedTemplateId}
          selectedTemplateName={selectedTemplate?.name || '默认模板'}
          onOpenTemplateManager={() => setTemplateManagerOpen(true)}
          notify={notify}
          onNotifyChange={setNotify}
          sidebarContent={sidebarContent}
          mobileOpen={sidebarOpen}
          onCloseMobile={() => setSidebarOpen(false)}
        />

        <main className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          <header className="flex flex-shrink-0 items-center gap-3 border-b border-slate-200 bg-white/72 px-3 py-3 backdrop-blur-xl sm:px-5">
            <button
              onClick={() => setSidebarOpen(true)}
              className="inline-flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-cyan-300 hover:text-cyan-700 lg:hidden"
              aria-label="历史记录"
            >
              <Menu className="h-5 w-5" />
            </button>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Stock Assistant</p>
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
            </div>
          </header>

          {(inputError || duplicateError || setupNeedsAction) ? (
            <div className="space-y-2 border-b border-slate-200 bg-white/58 px-3 py-3 sm:px-5">
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
            <div className="mx-auto flex w-full max-w-full flex-col gap-4">
              <div className="grid gap-4">
                <BatchPanel
                  templates={templates}
                  selectedTemplateId={selectedTemplateId}
                  onTemplateChange={setSelectedTemplateId}
                />
              </div>

              <Suspense fallback={<HomeAnalysisFallback />}>
                <HomeAnalysisCanvas
                  error={error}
                  onClearError={clearError}
                  isLoadingTaskStatus={isLoadingTaskStatus}
                  taskPreviewReport={taskPreviewReport}
                  isLoadingReport={isLoadingReport}
                  pendingAutoSelectCode={pendingAutoSelectCode}
                  selectedReport={selectedReport}
                  isAnalyzing={isAnalyzing}
                  onReanalyze={handleReanalyze}
                  onOpenMarkdownDrawer={openMarkdownDrawer}
                  reanalyzeLabel={reportText.reanalyze}
                  fullReportLabel={reportText.fullReport}
                />
              </Suspense>
            </div>
          </section>

        </main>
      </div>

      {markdownDrawerOpen && selectedReport?.meta.id ? (
        <Suspense fallback={null}>
          <ReportMarkdown
            recordId={selectedReport.meta.id}
            stockName={selectedReport.meta.stockName || ''}
            stockCode={selectedReport.meta.stockCode}
            reportLanguage={reportLanguage}
            onClose={closeMarkdownDrawer}
          />
        </Suspense>
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

function HomeAnalysisFallback() {
  return (
    <div className="flex items-center justify-center py-12">
      <div className="flex flex-col items-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan-400 border-t-transparent" />
        <p className="text-sm text-slate-400">加载分析面板...</p>
      </div>
    </div>
  );
}

export default HomePage;
