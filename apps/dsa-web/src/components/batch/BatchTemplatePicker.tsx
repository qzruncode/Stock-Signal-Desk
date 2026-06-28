import type { PromptTemplateItem } from '../../api/prompts';

interface BatchTemplatePickerProps {
  templates: PromptTemplateItem[];
  isLoading: boolean;
  selectedTemplateId: string;
  onTemplateChange: (templateId: string) => void;
}

export function BatchTemplatePicker({
  templates,
  isLoading,
  selectedTemplateId,
  onTemplateChange,
}: BatchTemplatePickerProps) {
  return (
    <div className="space-y-1">
      <label className="text-[10px] font-medium text-muted-text uppercase tracking-wider">
        提示词模板
      </label>
      {isLoading ? (
        <div className="h-9 animate-pulse rounded-lg bg-hover/50" />
      ) : templates.length === 0 ? (
        <p className="text-xs text-muted-text">暂无模板</p>
      ) : (
        <select
          value={selectedTemplateId}
          onChange={(e) => onTemplateChange(e.target.value)}
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
  );
}