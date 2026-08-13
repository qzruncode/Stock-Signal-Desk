import React, { useCallback, useId, useState } from 'react';
import { Eye } from 'lucide-react';
import { cn } from '../../utils/cn';
import { Input } from '../common/Input';
import { Select } from '../common/Select';
import type { SystemConfigFieldSchema, SystemConfigOption } from '../../types/systemConfig';

interface SettingsFieldProps {
  field: SystemConfigFieldSchema;
  value: string;
  onChange: (key: string, value: string) => void;
  isMasked?: boolean;
  showSensitiveValue?: boolean;
  compact?: boolean;
  showLabel?: boolean;
  showHint?: boolean;
  placeholder?: string;
}

function fieldOptions(field: SystemConfigFieldSchema): { value: string; label: string }[] {
  if (!field.options || field.options.length === 0) return [];
  return field.options.map((opt) => {
    if (typeof opt === 'string') return { value: opt, label: opt };
    return { value: (opt as SystemConfigOption).value, label: (opt as SystemConfigOption).label };
  });
}

function fieldExamples(field: SystemConfigFieldSchema): string | undefined {
  if (!field.examples || field.examples.length === 0) return undefined;
  return `例: ${field.examples.join(', ')}`;
}

export const SettingsField: React.FC<SettingsFieldProps> = ({
  field,
  value,
  onChange,
  isMasked = false,
  showSensitiveValue = false,
  compact = false,
  showLabel = true,
  showHint = true,
  placeholder,
}) => {
  const fieldId = useId();
  const [revealed, setRevealed] = useState(false);

  const handleChange = useCallback(
    (newValue: string) => {
      onChange(field.key, newValue);
    },
    [field.key, onChange],
  );

  const label = field.title ?? field.key;
  const description = field.description ?? '';
  const examples = fieldExamples(field);
  const hint = showHint ? (description || examples || undefined) : undefined;
  const visualLabel = showLabel ? label : undefined;

  // Masked sensitive field: show "Configured" badge with reveal button
  if (isMasked && field.isSensitive && !revealed) {
    return (
      <div className="flex flex-col gap-1.5">
        <label className={showLabel ? 'text-[13px] font-medium text-foreground' : 'sr-only'}>{label}</label>
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-success/30 bg-success/10 px-3 py-1.5 text-xs font-medium text-success">
            <span className="h-1.5 w-1.5 rounded-full bg-success" />
            已配置（隐藏）
          </span>
          <button
            type="button"
            onClick={() => {
              setRevealed(true);
              onChange(field.key, '');
            }}
            className="inline-flex items-center gap-1 rounded-lg border border-border/50 bg-elevated px-2.5 py-1.5 text-xs text-secondary-text transition hover:border-warning/40 hover:text-warning"
          >
            <Eye className="h-3.5 w-3.5" />
            修改
          </button>
        </div>
        {hint && <p className="text-xs text-secondary-text">{hint}</p>}
      </div>
    );
  }

  // Switch / boolean toggle
  if (field.uiControl === 'switch') {
    const isOn = value === 'true' || value === '1';
    return (
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col">
          <span className={cn('text-[13px] font-medium text-foreground', !showLabel && 'sr-only')}>{label}</span>
          {hint && <span className="text-xs text-secondary-text">{hint}</span>}
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={isOn}
          aria-label={label}
          onClick={() => handleChange(isOn ? 'false' : 'true')}
          className={cn(
            'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-200 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-cyan/15',
            isOn ? 'bg-cyan' : 'bg-border/60',
          )}
        >
          <span
            className={cn(
              'inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200',
              isOn ? 'translate-x-6' : 'translate-x-1',
            )}
          />
        </button>
      </div>
    );
  }

  // Textarea
  if (field.uiControl === 'textarea') {
    return (
      <div className="flex flex-col">
        {visualLabel && (
          <label htmlFor={fieldId} className={cn('font-medium text-foreground', compact ? 'mb-1 text-[13px]' : 'mb-2 text-sm')}>
            {visualLabel}
          </label>
        )}
        <textarea
          id={fieldId}
          aria-label={!showLabel ? label : undefined}
          value={value}
          onChange={(e) => handleChange(e.target.value)}
          rows={compact ? 2 : 3}
          className={cn(
            'input-surface input-focus-glow w-full border bg-transparent transition-all focus:outline-none disabled:cursor-not-allowed disabled:opacity-60',
            compact ? 'rounded-md px-3 py-2 text-xs shadow-none' : 'rounded-xl px-4 py-3 text-sm',
          )}
          placeholder={field.defaultValue ?? undefined}
        />
        {hint && <p className="mt-2 text-xs text-secondary-text">{hint}</p>}
      </div>
    );
  }

  // Select / dropdown
  if (field.uiControl === 'select') {
    const options = fieldOptions(field);
    return (
      <Select
        label={visualLabel}
        ariaLabel={label}
        value={value}
        onChange={handleChange}
        options={options}
        density={compact ? 'compact' : 'regular'}
        placeholder={field.defaultValue ?? '请选择'}
      />
    );
  }

  // Number
  if (field.uiControl === 'number') {
    const min = field.validation?.min as number | undefined;
    const max = field.validation?.max as number | undefined;
    return (
      <Input
        label={visualLabel}
        aria-label={!showLabel ? label : undefined}
        type="number"
        value={value}
        onChange={(e) => handleChange(e.target.value)}
        hint={hint}
        density={compact ? 'compact' : 'regular'}
        min={min}
        max={max}
        step={field.validation?.step as number | undefined}
        placeholder={placeholder ?? field.defaultValue ?? undefined}
      />
    );
  }

  // Password (sensitive text)
  if (field.uiControl === 'password' || field.isSensitive) {
    return (
      <Input
        label={visualLabel}
        aria-label={!showLabel ? label : undefined}
        type={showSensitiveValue ? 'text' : 'password'}
        value={revealed ? value : value}
        onChange={(e) => handleChange(e.target.value)}
        hint={hint}
        density={compact ? 'compact' : 'regular'}
        iconType="key"
        allowTogglePassword={!showSensitiveValue}
        placeholder={value ? '' : (placeholder ?? field.defaultValue ?? '输入敏感配置')}
      />
    );
  }

  // Default: text input
  return (
    <Input
      label={visualLabel}
      aria-label={!showLabel ? label : undefined}
      type="text"
      value={value}
      onChange={(e) => handleChange(e.target.value)}
      hint={hint}
      density={compact ? 'compact' : 'regular'}
      placeholder={placeholder ?? field.defaultValue ?? undefined}
    />
  );
};
