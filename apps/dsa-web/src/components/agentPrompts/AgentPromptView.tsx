import { useState } from 'react';
import { Loader2, MessageSquareText, Plus } from 'lucide-react';
import { Button, InlineAlert } from '../common';
import { useAgentPrompts } from '../../hooks/useAgentPrompts';
import type { AgentPromptTemplate } from '../../types/agentPrompts';
import { AgentPromptForm } from './AgentPromptForm';
import { AgentPromptList } from './AgentPromptList';

type FormState =
  | { mode: 'create' }
  | { mode: 'edit'; template: AgentPromptTemplate }
  | null;

/**
 * AI 助手 system prompt 模板管理视图，挂载在 /setting 页的「AI 助手 Prompt」分类下。
 */
export const AgentPromptView: React.FC = () => {
  const {
    status,
    templates,
    activePrompt,
    error,
    createPrompt,
    updatePrompt,
    deletePrompt,
    activatePrompt,
    mutating,
  } = useAgentPrompts();
  const [form, setForm] = useState<FormState>(null);

  const handleSubmit = async (values: { name: string; content: string }) => {
    try {
      if (form?.mode === 'edit') {
        await updatePrompt(form.template.id, values);
      } else {
        await createPrompt(values);
      }
      setForm(null);
    } catch {
      // 错误已由 hook 写入 error，这里不额外处理
    }
  };

  const handleDelete = async (templateId: number) => {
    try {
      await deletePrompt(templateId);
    } catch {
      // 错误已由 hook 写入 error
    }
  };

  const handleActivate = async (templateId: number) => {
    try {
      await activatePrompt(templateId);
    } catch {
      // 错误已由 hook 写入 error
    }
  };

  return (
    <section className="space-y-5">
      <header className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-primary/10 text-primary">
            <MessageSquareText className="h-5 w-5" />
          </span>
          <div>
            <h2 className="text-xl font-semibold text-foreground">AI 助手 Prompt</h2>
            <p className="text-xs text-secondary-text">
              配置 AI 助手的系统提示词，支持多模板切换生效
            </p>
          </div>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => setForm({ mode: 'create' })}
          disabled={form !== null}
        >
          <Plus className="h-4 w-4" />
          新建模板
        </Button>
      </header>

      {error && (
        <InlineAlert variant="danger" title="操作出错" message={error} />
      )}

      {status === 'loading' ? (
        <div className="flex min-h-[20vh] items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-cyan" />
        </div>
      ) : (
        <div className="terminal-card space-y-3 rounded-2xl p-5">
          {form && (
            <AgentPromptForm
              key={form.mode === 'edit' ? `edit-${form.template.id}` : 'create'}
              template={form.mode === 'edit' ? form.template : null}
              saving={mutating}
              onCancel={() => setForm(null)}
              onSubmit={handleSubmit}
            />
          )}
          <AgentPromptList
            templates={templates}
            fallbackPrompt={
              activePrompt?.isFallback ? { content: activePrompt.content } : null
            }
            mutating={mutating}
            onEdit={(tpl) => setForm({ mode: 'edit', template: tpl })}
            onDelete={handleDelete}
            onActivate={handleActivate}
          />
        </div>
      )}
    </section>
  );
};

export default AgentPromptView;
