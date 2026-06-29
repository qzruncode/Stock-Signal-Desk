import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, GitBranch, GripVertical, Layers3, Plus, Save, Trash2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { promptsApi, type PromptTemplateItem } from '../api/prompts';
import { Button, EmptyState, InlineAlert } from '../components/common';
import { useWatchlistGroups } from '../hooks/useWatchlistGroups';
import { cn } from '../utils/cn';
import { loadJsonFromStorage, saveJsonToStorage } from '../utils/storage';

interface WorkflowStage {
  id: string;
  name: string;
  templateId: string;
  inputMode: 'group' | 'previous';
  groupId: string;
  outputGroupName: string;
}

interface SavedWorkflow {
  name: string;
  stages: WorkflowStage[];
}

const WORKFLOW_STORAGE_KEY = 'dsa.batch.workflow.v1';

function makeStage(index: number, templateId = ''): WorkflowStage {
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: ['预筛', '风险筛', '买点筛'][index] || `第 ${index + 1} 轮`,
    templateId,
    inputMode: index === 0 ? 'group' : 'previous',
    groupId: '',
    outputGroupName: `工作流第${index + 1}轮输出`,
  };
}

function loadWorkflow(): SavedWorkflow | null {
  return loadJsonFromStorage<SavedWorkflow | null>(
    WORKFLOW_STORAGE_KEY,
    null,
    (value): value is SavedWorkflow | null => value === null || (
      Boolean(value)
      && typeof value === 'object'
      && Array.isArray((value as SavedWorkflow).stages)
    ),
  );
}

function saveWorkflow(workflow: SavedWorkflow) {
  saveJsonToStorage(WORKFLOW_STORAGE_KEY, workflow);
}

const WorkflowBuilderPage: React.FC = () => {
  const navigate = useNavigate();
  const initialWorkflow = useMemo(() => loadWorkflow(), []);
  const [templates, setTemplates] = useState<PromptTemplateItem[]>([]);
  const [workflowName, setWorkflowName] = useState(() => initialWorkflow?.name || '多轮筛选工作流');
  const [stages, setStages] = useState<WorkflowStage[]>(() => initialWorkflow?.stages || []);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { groups } = useWatchlistGroups();

  useEffect(() => {
    document.title = '工作流编排 - Stock-Signal-Desk';
    promptsApi.getPromptTemplates()
      .then((items) => {
        setTemplates(items);
        if (!initialWorkflow) {
          const defaultTemplate = items.find((item) => item.is_default) || items[0];
          setStages([
            makeStage(0, defaultTemplate?.id || ''),
            makeStage(1, defaultTemplate?.id || ''),
            makeStage(2, defaultTemplate?.id || ''),
          ]);
        }
      })
      .catch(() => setError('加载分析模型失败'));
  }, [initialWorkflow]);

  const updateStage = useCallback((id: string, patch: Partial<WorkflowStage>) => {
    setStages((current) => current.map((stage) => (stage.id === id ? { ...stage, ...patch } : stage)));
  }, []);

  const addStage = useCallback(() => {
    setStages((current) => [...current, makeStage(current.length, templates[0]?.id || '')]);
  }, [templates]);

  const removeStage = useCallback((id: string) => {
    setStages((current) => current.filter((stage) => stage.id !== id));
  }, []);

  const moveStage = useCallback((targetId: string) => {
    if (!draggingId || draggingId === targetId) return;
    setStages((current) => {
      const from = current.findIndex((stage) => stage.id === draggingId);
      const to = current.findIndex((stage) => stage.id === targetId);
      if (from < 0 || to < 0) return current;
      const next = [...current];
      const [item] = next.splice(from, 1);
      next.splice(to, 0, item);
      return next.map((stage, index) => ({
        ...stage,
        inputMode: index === 0 ? 'group' : stage.inputMode,
      }));
    });
  }, [draggingId]);

  const handleSave = useCallback(() => {
    if (stages.length === 0) {
      setError('至少需要一个筛选节点');
      return;
    }
    saveWorkflow({ name: workflowName.trim() || '多轮筛选工作流', stages });
    setError(null);
    setMessage('工作流已保存。当前版本支持可视化编排，下一步会接入按节点顺序自动跑批。');
  }, [stages, workflowName]);

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-base">
      <header className="border-b border-slate-200 bg-slate-50/90 px-4 py-3 backdrop-blur-xl">
        <div className="mx-auto flex w-full max-w-[1440px] flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <button
              type="button"
              onClick={() => navigate('/')}
              className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-subtle bg-surface text-muted-text transition hover:bg-hover hover:text-foreground"
              aria-label="返回"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Workflow</p>
              <h1 className="truncate text-lg font-semibold text-foreground">工作流编排</h1>
            </div>
          </div>
          <Button variant="secondary" size="sm" onClick={handleSave}>
            <Save className="h-4 w-4" />
            保存编排
          </Button>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-auto mt-3">
        <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-4">
          {error && (
            <InlineAlert
              variant="danger"
              message={error}
              action={<button type="button" onClick={() => setError(null)} className="text-xs">关闭</button>}
            />
          )}
          {message && (
            <InlineAlert
              variant="success"
              message={message}
              action={<button type="button" onClick={() => setMessage(null)} className="text-xs">关闭</button>}
            />
          )}

          <section className="rounded-xl border border-subtle bg-surface p-4">
            <div className="grid gap-3 lg:grid-cols-[minmax(220px,320px)_1fr]">
              <div>
                <label className="text-[10px] font-semibold uppercase tracking-wider text-muted-text">工作流名称</label>
                <input
                  value={workflowName}
                  onChange={(event) => setWorkflowName(event.target.value)}
                  className="mt-1 h-10 w-full rounded-lg border border-subtle bg-background px-3 text-sm text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/10"
                />
              </div>
              <div className="rounded-lg border border-dashed border-subtle bg-background/70 px-4 py-3 text-sm text-secondary-text">
                <GitBranch className="mr-2 inline h-4 w-4 text-cyan-700" />
                首轮从股票池分组读取；后续节点默认接收上一轮筛选通过股票，节点输出可保存为新的分组，继续给下一轮跑批使用。
              </div>
            </div>
          </section>

          {stages.length === 0 ? (
            <EmptyState title="还没有筛选节点" description="添加节点来编排预筛、风险筛、买点筛等多轮流程。" />
          ) : (
            <section className="grid gap-4 xl:grid-cols-[1fr_320px]">
              <div className="min-h-[28rem] overflow-x-auto rounded-xl border border-subtle bg-surface p-4">
                <div className="flex min-w-max items-stretch gap-4">
                  {stages.map((stage, index) => (
                    <React.Fragment key={stage.id}>
                      <div
                        draggable
                        onDragStart={() => setDraggingId(stage.id)}
                        onDragOver={(event) => {
                          event.preventDefault();
                          moveStage(stage.id);
                        }}
                        onDragEnd={() => setDraggingId(null)}
                        className={cn(
                          'flex w-[280px] flex-col gap-3 rounded-lg border border-subtle bg-background p-4 shadow-sm transition',
                          draggingId === stage.id && 'opacity-60 ring-2 ring-primary/20',
                        )}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2">
                            <GripVertical className="h-4 w-4 cursor-grab text-muted-text" />
                            <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">
                              第 {index + 1} 轮
                            </span>
                          </div>
                          <button
                            type="button"
                            onClick={() => removeStage(stage.id)}
                            className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-muted-text transition hover:bg-red-500/10 hover:text-red-600"
                            aria-label="删除节点"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>

                        <input
                          value={stage.name}
                          onChange={(event) => updateStage(stage.id, { name: event.target.value })}
                          className="h-9 rounded-lg border border-subtle bg-surface px-3 text-sm font-semibold text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/10"
                        />

                        <label className="space-y-1">
                          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-text">分析模型</span>
                          <select
                            value={stage.templateId}
                            onChange={(event) => updateStage(stage.id, { templateId: event.target.value })}
                            className="h-9 w-full rounded-lg border border-subtle bg-surface px-3 text-sm text-foreground outline-none"
                          >
                            {templates.map((template) => (
                              <option key={template.id} value={template.id}>{template.name}</option>
                            ))}
                          </select>
                        </label>

                        <label className="space-y-1">
                          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-text">输入股票池</span>
                          {index === 0 ? (
                            <select
                              value={stage.groupId}
                              onChange={(event) => updateStage(stage.id, { groupId: event.target.value })}
                              className="h-9 w-full rounded-lg border border-subtle bg-surface px-3 text-sm text-foreground outline-none"
                            >
                              <option value="">选择分组</option>
                              {groups.map((group) => (
                                <option key={group.id} value={group.id}>{group.name} ({group.codes.length})</option>
                              ))}
                            </select>
                          ) : (
                            <div className="rounded-lg border border-cyan-500/20 bg-cyan-500/10 px-3 py-2 text-sm text-cyan-800 dark:text-cyan-200">
                              上一轮筛选通过股票
                            </div>
                          )}
                        </label>

                        <label className="space-y-1">
                          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-text">输出分组名</span>
                          <input
                            value={stage.outputGroupName}
                            onChange={(event) => updateStage(stage.id, { outputGroupName: event.target.value })}
                            className="h-9 w-full rounded-lg border border-subtle bg-surface px-3 text-sm text-foreground outline-none"
                          />
                        </label>
                      </div>
                      {index < stages.length - 1 && (
                        <div className="flex items-center text-muted-text">
                          <div className="h-px w-10 bg-border" />
                          <div className="rounded-full border border-subtle bg-background px-2 py-1 text-[10px]">通过股</div>
                          <div className="h-px w-10 bg-border" />
                        </div>
                      )}
                    </React.Fragment>
                  ))}
                  <button
                    type="button"
                    onClick={addStage}
                    className="flex w-[180px] flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-subtle bg-background/70 text-sm text-muted-text transition hover:border-primary/40 hover:text-primary"
                  >
                    <Plus className="h-5 w-5" />
                    添加一轮
                  </button>
                </div>
              </div>

              <aside className="rounded-xl border border-subtle bg-surface p-4">
                <div className="flex items-center gap-2">
                  <Layers3 className="h-4 w-4 text-cyan-700" />
                  <h2 className="text-sm font-semibold text-foreground">执行计划</h2>
                </div>
                <div className="mt-4 space-y-3">
                  {stages.map((stage, index) => {
                    const template = templates.find((item) => item.id === stage.templateId);
                    const group = groups.find((item) => item.id === stage.groupId);
                    return (
                      <div key={stage.id} className="rounded-lg border border-subtle bg-background p-3 text-sm">
                        <p className="font-semibold text-foreground">{index + 1}. {stage.name || `第 ${index + 1} 轮`}</p>
                        <p className="mt-1 text-xs text-muted-text">模型：{template?.name || '未选择'}</p>
                        <p className="mt-1 text-xs text-muted-text">
                          输入：{index === 0 ? (group ? `${group.name} (${group.codes.length})` : '未选择分组') : '上一轮输出'}
                        </p>
                        <p className="mt-1 text-xs text-muted-text">输出：{stage.outputGroupName || '-'}</p>
                      </div>
                    );
                  })}
                </div>
              </aside>
            </section>
          )}
        </div>
      </main>
    </div>
  );
};

export default WorkflowBuilderPage;
