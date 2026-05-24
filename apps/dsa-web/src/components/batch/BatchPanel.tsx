import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'motion/react';
import { BarChart3, ChevronDown, ChevronRight, Clock, FileText, Loader2, Play, X } from 'lucide-react';
import { systemConfigApi } from '../../api/systemConfig';
import { useBatchStore } from '../../stores/batchStore';
import type { PromptTemplateItem } from '../../api/prompts';
import { cn } from '../../utils/cn';
import { Button, ApiErrorAlert } from '../common';

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

  const stockCodes = useMemo(
    () => (stockCodesProp && stockCodesProp.length > 0 ? stockCodesProp : configStockCodes),
    [stockCodesProp, configStockCodes],
  );

  const stopPollRef = useRef<(() => void) | null>(null);

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
    runs,
    selectedReportContent,
    isLoadingReport,
    schedule,
    error,
    loadTemplates,
    setSelectedTemplateId,
    triggerBatchRun,
    pollProgress,
    fetchRuns,
    viewReport,
    closeReport,
    fetchSchedule,
    updateSchedule,
    clearError,
  } = useBatchStore();

  const templates = templatesProp ?? storeTemplates;
  const selectedTemplateId = selectedTemplateIdProp ?? storeSelectedTemplateId;
  const handleTemplateChange = useCallback((templateId: string) => {
    setSelectedTemplateId(templateId);
    onTemplateChange?.(templateId);
  }, [onTemplateChange, setSelectedTemplateId]);

  useEffect(() => {
    if (!templatesProp) {
      void loadTemplates();
    }
  }, [loadTemplates, templatesProp]);

  useEffect(() => {
    if (selectedTemplateIdProp !== undefined) {
      setSelectedTemplateId(selectedTemplateIdProp);
    }
  }, [selectedTemplateIdProp, setSelectedTemplateId]);

  useEffect(() => {
    if (!collapsed) {
      void fetchRuns();
      void fetchSchedule();
    }
  }, [collapsed, fetchRuns, fetchSchedule]);

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
      if (stopPollRef.current) {
        stopPollRef.current();
      }
    };
  }, []);

  const handleTrigger = useCallback(async () => {
    if (selectedTemplateId) {
      setSelectedTemplateId(selectedTemplateId);
    }
    const ok = await triggerBatchRun(stockCodes);
    if (ok) {
      setCollapsed(false);
    }
  }, [selectedTemplateId, setSelectedTemplateId, triggerBatchRun, stockCodes]);

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

  const progressPercent = runStockCount > 0 ? Math.round((runCompleted / runStockCount) * 100) : 0;

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

              <div className="space-y-1">
                <label className="text-[10px] font-medium text-muted-text uppercase tracking-wider">
                  提示词模板
                </label>
                {isLoadingTemplates ? (
                  <div className="h-9 animate-pulse rounded-lg bg-hover/50" />
                ) : templates.length === 0 ? (
                  <p className="text-xs text-muted-text">暂无模板</p>
                ) : (
                  <select
                    value={selectedTemplateId}
                    onChange={(e) => handleTemplateChange(e.target.value)}
                    className="w-full rounded-lg border border-subtle bg-surface px-3 py-2 text-xs text-foreground focus:border-primary/40 focus:outline-none focus:ring-1 focus:ring-primary/20"
                  >
                    {templates.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}{t.is_default ? ' (默认)' : ''}
                      </option>
                    ))}
                  </select>
                )}
              </div>

              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  onClick={() => void handleTrigger()}
                  disabled={isRunning || stockCodes.length === 0}
                  isLoading={isRunning}
                  loadingText="运行中"
                  className="flex-1"
                >
                  <Play className="h-3.5 w-3.5" />
                  跑批 ({stockCodes.length} 只)
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={handleOpenSchedule}
                >
                  <Clock className="h-3.5 w-3.5" />
                </Button>
              </div>

              {isRunning && (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-text">
                      {currentStock ? `分析中: ${currentStock}` : '准备中...'}
                    </span>
                    <span className="font-mono text-foreground tabular-nums">
                      {runCompleted}/{runStockCount} ({progressPercent}%)
                    </span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-hover">
                    <motion.div
                      className="h-full rounded-full bg-primary"
                      initial={{ width: 0 }}
                      animate={{ width: `${Math.min(progressPercent, 100)}%` }}
                      transition={{ duration: 0.3 }}
                    />
                  </div>
                  <div className="flex gap-3 text-[10px] text-muted-text">
                    <span className="text-emerald-600 dark:text-emerald-400">成功 {runSuccess}</span>
                    <span className="text-red-600 dark:text-red-400">失败 {runFailed}</span>
                  </div>
                </div>
              )}

              {runs.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-[10px] font-medium text-muted-text uppercase tracking-wider">
                    跑批记录
                  </p>
                  <div className="max-h-[200px] overflow-y-auto space-y-1">
                    {runs.map((run) => (
                      <button
                        key={run.run_id}
                        type="button"
                        onClick={() => {
                          if (run.report_path) {
                            void viewReport(run.run_id);
                          }
                        }}
                        disabled={!run.report_path}
                        className={cn(
                          'flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition-colors hover:bg-hover/70',
                          !run.report_path && 'opacity-50 cursor-default'
                        )}
                      >
                        <FileText className="h-3.5 w-3.5 shrink-0 text-muted-text" />
                        <span className="flex-1 truncate">
                          {run.template_name || '未知模板'}
                        </span>
                        <span className="shrink-0 font-mono text-[10px] text-muted-text">
                          {run.success_count}/{run.stock_count}
                        </span>
                        <span className="shrink-0 text-[10px] text-muted-text">
                          {run.completed_at ? new Date(run.completed_at).toLocaleDateString('zh') : '-'}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <AnimatePresence>
                {selectedReportContent && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    className="overflow-hidden"
                  >
                    <div className="space-y-2 pt-2 border-t border-subtle">
                      <div className="flex items-center justify-between">
                        <p className="text-xs font-semibold text-foreground">汇总报告</p>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={closeReport}
                        >
                          <X className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                      {isLoadingReport ? (
                        <div className="flex items-center justify-center py-4">
                          <Loader2 className="h-4 w-4 animate-spin text-muted-text" />
                        </div>
                      ) : (
                        <pre className="max-h-[400px] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-background px-3 py-2 text-xs leading-relaxed text-secondary-text">
                          {selectedReportContent}
                        </pre>
                      )}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {showScheduleDialog && createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={() => setShowScheduleDialog(false)}>
          <div className="absolute inset-0 bg-black/40" />
          <div
            className="relative w-full max-w-sm rounded-2xl border border-subtle bg-surface p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold text-foreground">定时跑批设置</h3>
              <button
                type="button"
                onClick={() => setShowScheduleDialog(false)}
                className="rounded-lg p-1 text-muted-text hover:bg-hover hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-3">
              <label className="flex items-center gap-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  checked={scheduleEnabled}
                  onChange={(e) => setScheduleEnabled(e.target.checked)}
                  className="h-3.5 w-3.5 rounded border-border accent-primary"
                />
                启用每日定时跑批
              </label>

              {scheduleEnabled && (
                <div className="space-y-2">
                  <div className="flex gap-1.5">
                    <input
                      type="time"
                      value={newTime}
                      onChange={(e) => setNewTime(e.target.value)}
                      className="flex-1 rounded-lg border border-subtle bg-background px-2 py-1.5 text-xs"
                    />
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={handleAddTime}
                    >
                      添加
                    </Button>
                  </div>
                  {scheduleTimes.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {scheduleTimes.map((t) => (
                        <span
                          key={t}
                          className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary"
                        >
                          {t}
                          <button
                            type="button"
                            onClick={() => setScheduleTimes(scheduleTimes.filter((x) => x !== t))}
                            className="ml-0.5 hover:text-red-500"
                          >
                            <X className="h-3 w-3" />
                          </button>
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-muted-text">尚未添加时间点</p>
                  )}
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => setShowScheduleDialog(false)}
              >
                取消
              </Button>
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={handleSaveSchedule}
              >
                保存
              </Button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
};
