import React from 'react';
import { Save, RotateCcw, Brain } from 'lucide-react';
import { cn } from '../../utils/cn';
import { Button } from '../common';

interface QuickConfigBarProps {
  currentModel: string;
  thinkingEnabled: boolean;
  reasoningEffort: string;
  dirtyCount: number;
  saving: boolean;
  onThinkingToggle: () => void;
  onReasoningEffortChange: (value: string) => void;
  onSave: () => void;
  onReset: () => void;
}

export const QuickConfigBar: React.FC<QuickConfigBarProps> = ({
  currentModel,
  thinkingEnabled,
  reasoningEffort,
  dirtyCount,
  saving,
  onThinkingToggle,
  onReasoningEffortChange,
  onSave,
  onReset,
}) => {
  if (dirtyCount === 0) return null;

  return (
    <div className="settings-surface-strong sticky bottom-4 z-20 flex flex-wrap items-center gap-3 rounded-xl border border-warning/30 p-4 shadow-[0_-8px_32px_rgba(15,23,42,0.18)] backdrop-blur-xl">
      <div className="flex min-w-0 flex-1 items-center gap-2 text-sm">
        <span className="font-medium text-secondary-text">
          有 {dirtyCount} 项更改未保存
        </span>
        {currentModel && (
          <code className="rounded bg-cyan/10 px-1.5 py-0.5 text-xs text-cyan">
            {currentModel}
          </code>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onThinkingToggle}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-all',
            thinkingEnabled
              ? 'border-cyan/30 bg-cyan/10 text-cyan'
              : 'border-border/50 bg-elevated text-secondary-text hover:text-foreground',
          )}
          aria-pressed={thinkingEnabled}
        >
          <Brain className="h-3.5 w-3.5" />
          深度思考 {thinkingEnabled ? 'ON' : 'OFF'}
        </button>

        {thinkingEnabled && (
          <select
            value={reasoningEffort}
            onChange={(e) => onReasoningEffortChange(e.target.value)}
            className="rounded-lg border border-border/50 bg-elevated px-2 py-1.5 text-xs text-foreground focus:border-cyan/50 focus:outline-none"
            aria-label="Reasoning effort level"
          >
            <option value="auto">Auto</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        )}

        <Button variant="settings-secondary" size="sm" onClick={onReset} disabled={saving}>
          <RotateCcw className="h-4 w-4" />
          放弃更改
        </Button>
        <Button variant="settings-primary" size="sm" onClick={onSave} isLoading={saving} loadingText="保存中...">
          <Save className="h-4 w-4" />
          保存配置
        </Button>
      </div>
    </div>
  );
};
