import { useCallback, useEffect, useRef, useState } from 'react';
import { promptsApi, type PromptTemplateItem } from '../../api/prompts';
import { Button, ConfirmDialog } from '../common';

type TemplateManagerProps = {
  templates: PromptTemplateItem[];
  onTemplatesChange: (templates: PromptTemplateItem[]) => void;
  selectedTemplateId: string;
  onSelectTemplate: (id: string) => void;
  onClose: () => void;
};

type FormMode = { mode: 'create' } | { mode: 'edit'; template: PromptTemplateItem };

const MAX_CONTENT_PREVIEW = 120;

export function TemplateManager({
  templates,
  onTemplatesChange,
  selectedTemplateId,
  onSelectTemplate,
  onClose,
}: TemplateManagerProps) {
  const [form, setForm] = useState<FormMode | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PromptTemplateItem | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const nameRef = useRef<HTMLInputElement>(null);
  const contentRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (form && nameRef.current) {
      nameRef.current.focus();
    }
  }, [form]);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (form || deleteTarget) {
          setForm(null);
          setDeleteTarget(null);
          setError(null);
        } else {
          onClose();
        }
      }
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [form, deleteTarget, onClose]);

  const handleCreate = useCallback(async () => {
    if (!nameRef.current || !contentRef.current) return;
    const name = nameRef.current.value.trim();
    const content = contentRef.current.value.trim();
    if (!name || !content) {
      setError('请填写模板名称和内容');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const created = await promptsApi.createPromptTemplate({ name, content });
      onTemplatesChange([...templates, created]);
      setForm(null);
      if (!selectedTemplateId) {
        onSelectTemplate(created.id);
      }
    } catch {
      setError('创建失败，请重试');
    } finally {
      setSaving(false);
    }
  }, [templates, selectedTemplateId, onTemplatesChange, onSelectTemplate]);

  const handleUpdate = useCallback(async () => {
    if (!nameRef.current || !contentRef.current || form?.mode !== 'edit') return;
    const name = nameRef.current.value.trim();
    const content = contentRef.current.value.trim();
    if (!name || !content) {
      setError('请填写模板名称和内容');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await promptsApi.updatePromptTemplate(form.template.id, { name, content });
      onTemplatesChange(templates.map((t) => (t.id === updated.id ? updated : t)));
      setForm(null);
    } catch {
      setError('更新失败，请重试');
    } finally {
      setSaving(false);
    }
  }, [form, templates, onTemplatesChange]);

  const handleDelete = useCallback(async () => {
    if (!deleteTarget) return;
    setSaving(true);
    try {
      await promptsApi.deletePromptTemplate(deleteTarget.id);
      const next = templates.filter((t) => t.id !== deleteTarget.id);
      onTemplatesChange(next);
      if (selectedTemplateId === deleteTarget.id) {
        const defaultTpl = next.find((t) => t.is_default) || next[0];
        if (defaultTpl) onSelectTemplate(defaultTpl.id);
      }
      setDeleteTarget(null);
    } catch {
      setError('删除失败，请重试');
    } finally {
      setSaving(false);
    }
  }, [deleteTarget, templates, selectedTemplateId, onTemplatesChange, onSelectTemplate]);

  const handleSetDefault = useCallback(async (template: PromptTemplateItem) => {
    try {
      const updated = await promptsApi.updatePromptTemplate(template.id, { is_default: true });
      onTemplatesChange(
        templates.map((t) => ({
          ...t,
          is_default: t.id === updated.id,
        })),
      );
    } catch {
      setError('设置默认失败');
    }
  }, [templates, onTemplatesChange]);

  const handleSelectAndClose = useCallback((id: string) => {
    onSelectTemplate(id);
    onClose();
  }, [onSelectTemplate, onClose]);

  const formInitialName = form?.mode === 'edit' ? form.template.name : '';
  const formInitialContent = form?.mode === 'edit' ? form.template.content : '';

  return (
    <div className="fixed inset-0 z-50" role="presentation">
      <div
        className="absolute inset-0 bg-background/80 backdrop-blur-sm"
        onClick={onClose}
      />

      <div className="absolute inset-y-0 right-0 flex w-full max-w-2xl">
        <div
          role="dialog"
          aria-modal="true"
          aria-label="管理分析模板"
          className="relative flex w-full flex-col bg-card border-l border-border/80 animate-slide-in-right"
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-border/60 px-6 py-4">
            <div>
              <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-text">
                TEMPLATE MANAGER
              </span>
              <h2 className="mt-1 text-lg font-semibold text-foreground">管理分析模板</h2>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-border/70 bg-card/80 text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
              aria-label="关闭"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          {/* Error banner */}
          {error && (
            <div className="mx-6 mt-4 flex items-center gap-2 rounded-xl border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
              <svg className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              {error}
              <button
                type="button"
                className="ml-auto text-danger/70 hover:text-danger"
                onClick={() => setError(null)}
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          )}

          {/* Body */}
          <div className="flex-1 overflow-y-auto p-6">
            {/* New template button */}
            {!form && (
              <Button
                variant="primary"
                size="sm"
                onClick={() => setForm({ mode: 'create' })}
                className="mb-4 w-full"
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
                新建模板
              </Button>
            )}

            {/* Create/Edit form */}
            {form && (
              <div className="mb-4 rounded-xl border border-border/60 bg-surface/40 p-4">
                <h3 className="text-sm font-semibold text-foreground mb-3">
                  {form.mode === 'create' ? '新建模板' : '编辑模板'}
                </h3>
                <label className="block text-xs text-secondary-text mb-1">模板名称</label>
                <input
                  ref={nameRef}
                  type="text"
                  defaultValue={formInitialName}
                  placeholder="例如：综合多维分析"
                  className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-foreground placeholder:text-muted-text focus:border-cyan/50 focus:outline-none focus:ring-1 focus:ring-cyan/20 mb-3"
                />
                <label className="block text-xs text-secondary-text mb-1">提示词内容</label>
                <textarea
                  ref={contentRef}
                  defaultValue={formInitialContent}
                  placeholder="输入提示词内容..."
                  rows={10}
                  className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-foreground placeholder:text-muted-text focus:border-cyan/50 focus:outline-none focus:ring-1 focus:ring-cyan/20 font-mono resize-y"
                />
                <div className="flex items-center justify-end gap-2 mt-3">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => { setForm(null); setError(null); }}
                  >
                    取消
                  </Button>
                  <Button
                    variant="primary"
                    size="sm"
                    isLoading={saving}
                    loadingText="保存中..."
                    onClick={form.mode === 'create' ? handleCreate : handleUpdate}
                  >
                    {form.mode === 'create' ? '创建' : '保存'}
                  </Button>
                </div>
              </div>
            )}

            {/* Template list */}
            {templates.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <svg className="h-10 w-10 text-muted-text mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <p className="text-sm text-secondary-text">暂无分析模板</p>
                <p className="text-xs text-muted-text mt-1">点击上方按钮创建第一个模板</p>
              </div>
            ) : (
              <div className="space-y-2">
                {templates.map((tpl) => {
                  const isSelected = tpl.id === selectedTemplateId;
                  const isExpanded = tpl.id === expandedId;
                  return (
                    <div
                      key={tpl.id}
                      className={`rounded-xl border transition-colors ${
                        isSelected
                          ? 'border-cyan/40 bg-cyan/5'
                          : 'border-border/60 bg-surface/40 hover:border-border'
                      }`}
                    >
                      {/* Card header */}
                      <div className="flex items-center gap-3 px-4 py-3">
                        <button
                          type="button"
                          className="flex-1 min-w-0 text-left"
                          onClick={() => handleSelectAndClose(tpl.id)}
                        >
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-foreground truncate">
                              {tpl.name}
                            </span>
                            {tpl.is_default && (
                              <span className="shrink-0 rounded-full bg-cyan/15 px-2 py-0.5 text-[10px] font-medium text-cyan">
                                默认
                              </span>
                            )}
                            {isSelected && (
                              <span className="shrink-0 rounded-full bg-emerald/15 px-2 py-0.5 text-[10px] font-medium text-emerald">
                                使用中
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-muted-text mt-1 truncate">
                            {tpl.content.slice(0, MAX_CONTENT_PREVIEW)}
                            {tpl.content.length > MAX_CONTENT_PREVIEW ? '...' : ''}
                          </p>
                        </button>

                        <div className="flex shrink-0 items-center gap-1">
                          <button
                            type="button"
                            onClick={() => setExpandedId(isExpanded ? null : tpl.id)}
                            className="rounded-lg p-1.5 text-muted-text hover:bg-hover hover:text-foreground transition-colors"
                            title={isExpanded ? '收起' : '展开'}
                          >
                            <svg className={`h-4 w-4 transition-transform ${isExpanded ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                            </svg>
                          </button>
                          <button
                            type="button"
                            onClick={() => setForm({ mode: 'edit', template: tpl })}
                            className="rounded-lg p-1.5 text-muted-text hover:bg-hover hover:text-foreground transition-colors"
                            title="编辑"
                          >
                            <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                            </svg>
                          </button>
                          {!tpl.is_default && (
                            <button
                              type="button"
                              onClick={() => handleSetDefault(tpl)}
                              className="rounded-lg p-1.5 text-muted-text hover:bg-hover hover:text-amber-400 transition-colors"
                              title="设为默认"
                            >
                              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z" />
                              </svg>
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => setDeleteTarget(tpl)}
                            className="rounded-lg p-1.5 text-muted-text hover:bg-hover hover:text-danger transition-colors"
                            title="删除"
                          >
                            <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          </button>
                        </div>
                      </div>

                      {/* Expanded content */}
                      {isExpanded && (
                        <div className="border-t border-border/40 px-4 py-3">
                          <div className="rounded-lg bg-surface/60 p-3">
                            <pre className="text-xs text-foreground/80 whitespace-pre-wrap font-mono leading-relaxed">
                              {tpl.content}
                            </pre>
                          </div>
                          <div className="mt-2 flex items-center gap-3 text-[10px] text-muted-text">
                            <span>创建于 {new Date(tpl.created_at).toLocaleString('zh-CN')}</span>
                            <span>更新于 {new Date(tpl.updated_at).toLocaleString('zh-CN')}</span>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Delete confirmation */}
      <ConfirmDialog
        isOpen={deleteTarget !== null}
        title="删除模板"
        message={`确认删除模板「${deleteTarget?.name}」吗？删除后将不可恢复。`}
        confirmText={saving ? '删除中...' : '确认删除'}
        cancelText="取消"
        isDanger
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
