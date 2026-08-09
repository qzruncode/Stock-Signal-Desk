import { useForm } from 'react-hook-form';
import { Button } from '../common';
import type { AgentPromptTemplate } from '../../types/agentPrompts';

export interface AgentPromptFormProps {
  /** 编辑模式时传入目标模板；新建模式传 null。 */
  template: AgentPromptTemplate | null;
  saving: boolean;
  onCancel: () => void;
  onSubmit: (values: { name: string; content: string }) => void;
}

interface PromptFormValues {
  name: string;
  content: string;
}

/**
 * prompt 模板新建/编辑表单。
 * 父组件通过 key 在切换编辑目标时 remount 本组件，从而重置初始值。
 */
export const AgentPromptForm: React.FC<AgentPromptFormProps> = ({
  template,
  saving,
  onCancel,
  onSubmit,
}) => {
  const {
    register,
    handleSubmit,
    formState: { errors, isValid },
  } = useForm<PromptFormValues>({
    defaultValues: {
      name: template?.name ?? '',
      content: template?.content ?? '',
    },
    mode: 'onChange',
  });

  const submit = (values: PromptFormValues) => {
    onSubmit({ name: values.name.trim(), content: values.content });
  };

  return (
    <form onSubmit={handleSubmit(submit)} className="mb-4 rounded-xl border border-border/60 bg-surface/40 p-4">
      <h3 className="mb-3 text-sm font-semibold text-foreground">
        {template ? '编辑模板' : '新建模板'}
      </h3>
      <label className="mb-1 block text-xs text-secondary-text">模板名称</label>
      <input
        type="text"
        {...register('name', { required: '请输入模板名称' })}
        placeholder="例如：A 股分析助手默认"
        className="mb-3 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-foreground placeholder:text-muted-text focus:border-cyan/50 focus:outline-none focus:ring-1 focus:ring-cyan/20"
      />
      {errors.name ? <p className="-mt-2 mb-3 text-xs text-danger">{errors.name.message}</p> : null}
      <label className="mb-1 block text-xs text-secondary-text">提示词内容（system prompt）</label>
      <textarea
        {...register('content')}
        placeholder="输入提示词内容..."
        rows={12}
        className="w-full resize-y rounded-lg border border-border bg-surface px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted-text focus:border-cyan/50 focus:outline-none focus:ring-1 focus:ring-cyan/20"
      />
      <div className="mt-3 flex items-center justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={saving}>
          取消
        </Button>
        <Button
          variant="primary"
          size="sm"
          isLoading={saving}
          loadingText="保存中..."
          type="submit"
          disabled={!isValid}
        >
          {template ? '保存' : '创建'}
        </Button>
      </div>
    </form>
  );
};

export default AgentPromptForm;
