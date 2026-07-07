import { useState } from 'react';
import { ChevronDown, ChevronRight, Pencil, Trash2, Zap } from 'lucide-react';
import { Button, ConfirmDialog } from '../common';
import type { AgentPromptTemplate } from '../../types/agentPrompts';

export interface AgentPromptFallbackPrompt {
  /** 系统默认 prompt 的正文（回落到源码常量时）。 */
  content: string;
}

export interface AgentPromptListProps {
  templates: AgentPromptTemplate[];
  /** 回落到系统默认 prompt 时传入，在列表顶部以只读条目展示其内容。 */
  fallbackPrompt: AgentPromptFallbackPrompt | null;
  /** 操作进行中（禁用按钮）。 */
  mutating: boolean;
  onEdit: (template: AgentPromptTemplate) => void;
  onDelete: (templateId: number) => void;
  onActivate: (templateId: number) => void;
}

/**
 * 系统默认 prompt 只读条目：fallback 时展示源码内置 system prompt 的内容，
 * 可展开查看，不可编辑/删除/激活。
 */
const FallbackPromptItem: React.FC<{
  content: string;
  expanded: boolean;
  onToggle: () => void;
}> = ({ content, expanded, onToggle }) => (
  <div className="rounded-xl border border-cyan/40 bg-cyan/5">
    <div className="flex items-center gap-3 px-4 py-3">
      <button
        type="button"
        onClick={onToggle}
        className="flex min-w-0 flex-1 items-center gap-2 text-left"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-secondary-text" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-secondary-text" />
        )}
        <span className="truncate text-sm font-medium text-foreground">系统默认</span>
        <span className="rounded-full bg-cyan/15 px-2 py-0.5 text-[10px] text-cyan">
          生效中
        </span>
        <span className="shrink-0 text-[11px] text-muted-text">源码内置</span>
      </button>
    </div>
    {expanded && (
      <div className="border-t border-border/40 px-4 py-3">
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-secondary-text">
          {content || '（空内容）'}
        </pre>
      </div>
    )}
  </div>
);

/**
 * prompt 模板列表：展示名称、生效标记、更新时间，支持展开看内容、编辑、删除、设为生效。
 */
export const AgentPromptList: React.FC<AgentPromptListProps> = ({
  templates,
  fallbackPrompt,
  mutating,
  onEdit,
  onDelete,
  onActivate,
}) => {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AgentPromptTemplate | null>(null);

  if (templates.length === 0 && !fallbackPrompt) {
    return (
      <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border/60 py-12 text-center">
        <p className="mb-1 text-sm text-secondary-text">暂无 prompt 模板</p>
        <p className="text-xs text-muted-text">点击右上角「新建模板」创建</p>
      </div>
    );
  }

  return (
    <>
      <div className="space-y-2">
        {fallbackPrompt && (
          <FallbackPromptItem
            content={fallbackPrompt.content}
            expanded={expandedId === 'fallback'}
            onToggle={() =>
              setExpandedId(expandedId === 'fallback' ? null : 'fallback')
            }
          />
        )}

        {templates.map((tpl) => {
          const isExpanded = expandedId === String(tpl.id);
          return (
            <div
              key={tpl.id}
              className={`rounded-xl border ${
                tpl.isActive
                  ? 'border-cyan/40 bg-cyan/5'
                  : 'border-border/60 bg-surface/30'
              }`}
            >
              <div className="flex items-center gap-3 px-4 py-3">
                <button
                  type="button"
                  onClick={() => setExpandedId(isExpanded ? null : String(tpl.id))}
                  className="flex min-w-0 flex-1 items-center gap-2 text-left"
                >
                  {isExpanded ? (
                    <ChevronDown className="h-4 w-4 shrink-0 text-secondary-text" />
                  ) : (
                    <ChevronRight className="h-4 w-4 shrink-0 text-secondary-text" />
                  )}
                  <span className="truncate text-sm font-medium text-foreground">
                    {tpl.name}
                  </span>
                  {tpl.isActive && (
                    <span className="rounded-full bg-cyan/15 px-2 py-0.5 text-[10px] text-cyan">
                      生效中
                    </span>
                  )}
                  {tpl.updatedAt && (
                    <span className="shrink-0 text-[11px] text-muted-text">
                      {tpl.updatedAt.slice(0, 10)}
                    </span>
                  )}
                </button>
                <div className="flex shrink-0 items-center gap-1">
                  {!tpl.isActive && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={mutating}
                      onClick={() => onActivate(tpl.id)}
                    >
                      <Zap className="h-3.5 w-3.5" />
                      设为生效
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={mutating}
                    onClick={() => onEdit(tpl)}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                    编辑
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={mutating}
                    onClick={() => setDeleteTarget(tpl)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    删除
                  </Button>
                </div>
              </div>
              {isExpanded && (
                <div className="border-t border-border/40 px-4 py-3">
                  <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-secondary-text">
                    {tpl.content || '（空内容）'}
                  </pre>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <ConfirmDialog
        isOpen={deleteTarget !== null}
        title="删除模板"
        message={`确定删除模板「${deleteTarget?.name ?? ''}」吗？${
          deleteTarget?.isActive ? '该模板当前生效，删除后将回落系统默认 prompt。' : ''
        }`}
        confirmText="删除"
        cancelText="取消"
        isDanger
        onConfirm={() => {
          if (deleteTarget) {
            onDelete(deleteTarget.id);
            setDeleteTarget(null);
          }
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </>
  );
};

export default AgentPromptList;
