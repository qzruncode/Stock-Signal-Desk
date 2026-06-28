import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { BarChart3, ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import { systemConfigApi } from '../../api/systemConfig';
import { useBatchStore } from '../../stores/batchStore';
import type { BatchAnalysisMode } from '../../api/batch';
import type { PromptTemplateItem } from '../../api/prompts';
import { cn } from '../../utils/cn';
import { useWatchlistGroups } from '../../hooks/useWatchlistGroups';
import { ApiErrorAlert } from '../common';
import BatchScheduleDialog from './BatchScheduleDialog';
import { BatchModeSelector } from './BatchModeSelector';
import { BatchTemplatePicker } from './BatchTemplatePicker';
import { BatchStockScope } from './BatchStockScope';
import { BatchControlBar } from './BatchControlBar';
import { BatchProgressPanel } from './BatchProgressPanel';
import { BatchRunHistory } from './BatchRunHistory';

interface BatchPanelProps {
  stockCodes?: string[];
  templates?: PromptTemplateItem[];
  selectedTemplateId?: string;
  onTemplateChange?: (templateId: string) => void;
  className?: string;
}

export const BatchPanel: React.FC<BatchPanelProps> = ({
  stockCodes: stockCodesProp,
  templates: templatesProp,
  selectedTemplateId: selectedTemplateIdProp,
  onTemplateChange,
  className,
}) => {
  const [collapsed, setCollapsed] = useState(true);
  const [analysisMode, setAnalysisMode] = useState<BatchAnalysisMode>('template');
  const [forceRefresh, setForceRefresh] = useState(false);
  const [showScheduleDialog, setShowScheduleDialog] = useState(false);
  const [scheduleTimes, setScheduleTimes] = useState<string[]>([]);
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [newTime, setNewTime] = useState('09:00');
  const [configStockCodes, setConfigStockCodes] = useState<string[]>([]);

  useEffect(() => {
    if (stockCodesProp && stockCodesProp.length > 0) return;
    let active = true;
    systemConfigApi.getConfig(false)
      .then((config) => {
        if (!active) return;
        const stockListItem = config.items.find((item) => item.key === 'STOCK_LIST');
        const raw = stockListItem?.value || '';
        const codes = raw.split(',').map((c) => c.trim()).filter(Boolean);
        setConfigStockCodes(codes);
      })
      .catch(() => {
        if (active) setConfigStockCodes([]);
      });
    return () => { active = false; };
  }, [stockCodesProp]);

  const baseStockCodes = useMemo(
    () => (stockCodesProp && stockCodesProp.length > 0 ? stockCodesProp : configStockCodes),
    [stockCodesProp, configStockCodes],
  );

  const [selectedGroupId, setSelectedGroupId] = useState('all');
  const stopPollRef = useRef<(() => void) | null>(null);
  const { groups: watchlistGroups } = useWatchlistGroups();

  const {
    templates: storeTemplates,
    selectedTemplateId: storeSelectedTemplateId,
    isLoadingTemplates,
    isRunning,
    runStockCount,
    runCompleted,
    runSuccess,
    runFailed,
    currentStock,
    currentMessage,
    runStatus,
    runs,
    schedule,
    error,
    loadTemplates,
    setSelectedTemplateId,
    triggerBatchRun,
    resumeBatchRun,
    pauseBatchRun,
    continueBatchRun,
    stopBatchRun,
    pollProgress,
    fetchRuns,
    deleteRun,
    updateSchedule,
    clearError,
    syncCurrentProgress,
    fetchSchedule,
  } = useBatchStore();

  const templates = templatesProp ?? storeTemplates;
  const selectedTemplateId = selectedTemplateIdProp ?? storeSelectedTemplateId;

  const handleTemplateChange = useCallback((templateId: string) => {
    setSelectedTemplateId(templateId);
    onTemplateChange?.(templateId);
  }, [onTemplateChange, setSelectedTemplateId]);

  useEffect(() => {
    if (!templatesProp) void loadTemplates();
  }, [loadTemplates, templatesProp]);

  useEffect(() => {
    if (selectedTemplateIdProp !== undefined) {
      setSelectedTemplateId(selectedTemplateIdProp);
    }
  }, [selectedTemplateIdProp, setSelectedTemplateId]);

  useEffect(() => {
    if (!collapsed) {
      syncCurrentProgress();
      fetchRuns();
      fetchSchedule();
    }
  }, [collapsed, fetchRuns, fetchSchedule, syncCurrentProgress]);

  useEffect(() => {
    if (isRunning) {
      stopPollRef.current = pollProgress();
    }
    return () => {
      if (stopPollRef.current) {
        stopPollRef.current();
        stopPollRef.current = null;
      }
    };
  }, [isRunning, pollProgress]);

  useEffect(() => {
    return () => {
      if (stopPollRef.current) stopPollRef.current();
    };
  }, []);

  const stockCodes = useMemo(() => {
    if (stockCodesProp && stockCodesProp.length > 0) return baseStockCodes;
    if (selectedGroupId === 'all') return baseStockCodes;
    const group = watchlistGroups.find((item) => item.id === selectedGroupId);
    return group?.codes || [];
  }, [baseStockCodes, selectedGroupId, stockCodesProp, watchlistGroups]);

  const handleTrigger = useCallback(async () => {
    if (analysisMode === 'template' && !selectedTemplateId) return;
    const ok = await triggerBatchRun(stockCodes, { analysisMode, forceRefresh });
    if (ok) setCollapsed(false);
  }, [analysisMode, forceRefresh, selectedTemplateId, triggerBatchRun, stockCodes]);

  const handleOpenSchedule = useCallback(() => {
    if (schedule) {
      setScheduleEnabled(schedule.enabled);
      setScheduleTimes(schedule.times || []);
    } else {
      setScheduleEnabled(false);
      setScheduleTimes([]);
    }
    setShowScheduleDialog(true);
  }, [schedule]);

  const handleSaveSchedule = useCallback(() => {
    const templateId = selectedTemplateId || schedule?.template_id || '';
    if (!templateId) return;
    void updateSchedule({
      enabled: scheduleEnabled,
      times: scheduleTimes.filter(Boolean),
      template_id: templateId,
    });
    setShowScheduleDialog(false);
  }, [scheduleEnabled, scheduleTimes, selectedTemplateId, schedule, updateSchedule]);

  const handleAddTime = useCallback(() => {
    if (newTime && !scheduleTimes.includes(newTime)) {
      setScheduleTimes([...scheduleTimes, newTime].sort());
    }
  }, [newTime, scheduleTimes]);

  const isPaused = runStatus === 'paused';
  const isStopping = runStatus === 'stopping';

  return (
    <div className={cn('rounded-xl border border-subtle bg-surface/70 shadow-sm', className)}>
      <button
        type="button"
        onClick={() => setCollapsed(!collapsed)}
        className="flex w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-hover/50 rounded-t-xl"
      >
        {collapsed ? (
          <ChevronRight className="h-4 w-4 text-muted-text" />
        ) : (
          <ChevronDown className="h-4 w-4 text-muted-text" />
        )}
        <BarChart3 className="h-4 w-4 text-muted-text" />
        <span className="text-sm font-semibold text-foreground">批量分析</span>
        {isRunning && (
          <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
            <Loader2 className="h-3 w-3 animate-spin" />
            {runCompleted}/{runStockCount}
          </span>
        )}
        {!isRunning && runs.length > 0 && collapsed && (
          <span className="ml-auto text-xs text-muted-text">{runs.length} 次记录</span>
        )}
      </button>

      <AnimatePresence>
        {!collapsed && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-4 space-y-3">
              {error && (
                <ApiErrorAlert error={error} className="text-xs" onDismiss={clearError} />
              )}

              <BatchModeSelector
                analysisMode={analysisMode}
                forceRefresh={forceRefresh}
                isRunning={isRunning}
                onModeChange={setAnalysisMode}
                onForceRefreshChange={setForceRefresh}
              />

              {analysisMode === 'template' && (
                <BatchTemplatePicker
                  templates={templates}
                  isLoading={isLoadingTemplates}
                  selectedTemplateId={selectedTemplateId}
                  onTemplateChange={handleTemplateChange}
                />
              )}

              {!stockCodesProp && (
                <BatchStockScope
                  baseCount={baseStockCodes.length}
                  selectedGroupId={selectedGroupId}
                  onGroupChange={setSelectedGroupId}
                />
              )}

              <BatchControlBar
                isRunning={isRunning}
                stockCount={stockCodes.length}
                analysisMode={analysisMode}
                onTrigger={handleTrigger}
                onOpenSchedule={handleOpenSchedule}
              />

              <BatchProgressPanel
                isRunning={isRunning}
                isPaused={isPaused}
                isStopping={isStopping}
                runCompleted={runCompleted}
                runStockCount={runStockCount}
                runSuccess={runSuccess}
                runFailed={runFailed}
                currentStock={currentStock}
                currentMessage={currentMessage}
                onTogglePause={() => (isPaused ? continueBatchRun() : pauseBatchRun())}
                onStop={stopBatchRun}
              />

              <BatchRunHistory
                runs={runs}
                isRunning={isRunning}
                stockCodes={stockCodes}
                onResume={(runId) => resumeBatchRun(runId, stockCodes)}
                onDelete={deleteRun}
              />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {showScheduleDialog && (
        <BatchScheduleDialog
          enabled={scheduleEnabled}
          onEnabledChange={setScheduleEnabled}
          times={scheduleTimes}
          onTimesChange={setScheduleTimes}
          newTime={newTime}
          onNewTimeChange={setNewTime}
          onAddTime={handleAddTime}
          onSave={handleSaveSchedule}
          onClose={() => setShowScheduleDialog(false)}
        />
      )}
    </div>
  );
};