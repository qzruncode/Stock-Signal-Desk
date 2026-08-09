import { useCallback, useEffect, useMemo, useState } from 'react';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { ChevronDown, ClipboardPaste, Loader2, RotateCcw, Save } from 'lucide-react';
import { Button, InlineAlert } from '../common';
import { SettingsField } from './SettingsField';
import {
  SystemConfigConflictError,
  SystemConfigValidationError,
  systemConfigApi,
} from '../../api/systemConfig';
import type {
  SystemConfigCategorySchema,
  SystemConfigFieldSchema,
  SystemConfigResponse,
  SystemConfigSchemaResponse,
} from '../../types/systemConfig';

const TARGET_KEYS = [
  'ANTHROPIC_BASE_URL',
  'ANTHROPIC_AUTH_TOKEN',
  'CLAUDE_CODE_AUTO_COMPACT_WINDOW',
  'ANTHROPIC_MODEL',
  'ANTHROPIC_DEFAULT_SONNET_MODEL',
  'ANTHROPIC_DEFAULT_OPUS_MODEL',
  'ANTHROPIC_DEFAULT_HAIKU_MODEL',
  'LLM_THINKING_ENABLED',
  'LLM_REASONING_EFFORT',
  'LLM_TEMPERATURE',
] as const;

const TARGET_KEY_SET = new Set<string>(TARGET_KEYS);

type SaveStatus = { type: 'success' | 'error'; message: string } | null;
type ModelFormValues = Record<string, string>;

function collectValuesFromPaste(text: string): Record<string, string> | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  let data: unknown;
  try {
    data = JSON.parse(trimmed);
  } catch {
    return null;
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;

  const matched: Record<string, string> = {};
  for (const [key, value] of Object.entries(data as Record<string, unknown>)) {
    const upperKey = key.toUpperCase();
    if (TARGET_KEY_SET.has(upperKey)) {
      matched[upperKey] = String(value ?? '');
    }
  }
  return Object.keys(matched).length > 0 ? matched : null;
}

export const ModelSettingsView: React.FC = () => {
  const [schema, setSchema] = useState<SystemConfigSchemaResponse | null>(null);
  const [config, setConfig] = useState<SystemConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>(null);
  const { control, getValues, handleSubmit, reset, setValue } = useForm<ModelFormValues>({
    defaultValues: { pasteText: '' },
  });
  const watchedFieldValues = useWatch({ control });
  const fieldValues = useMemo(() => watchedFieldValues ?? {}, [watchedFieldValues]);

  const [pasteOpen, setPasteOpen] = useState(false);

  // 表单 -> JSON：fieldValues 变化时把 7 个目标字段拼成 JSON 回显
  useEffect(() => {
    const obj: Record<string, string> = {};
    for (const key of TARGET_KEYS) {
      if (fieldValues[key]) obj[key] = fieldValues[key];
    }
    const canonical = Object.keys(obj).length > 0 ? JSON.stringify(obj, null, 2) : '';
    if (getValues('pasteText') !== canonical) {
      setValue('pasteText', canonical, { shouldDirty: false });
    }
  }, [fieldValues, getValues, setValue]);

  // JSON -> 表单：textarea 输入时实时解析，匹配字段写入 fieldValues
  const handlePasteChange = useCallback((text: string) => {
    setValue('pasteText', text, { shouldDirty: false });
    const matched = collectValuesFromPaste(text);
    if (!matched) return;
    for (const key of TARGET_KEYS) {
      setValue(key, key in matched ? matched[key] : '', { shouldDirty: true });
    }
    setSaveStatus(null);
  }, [setValue]);

  const fetchConfig = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [schemaRes, configRes] = await Promise.all([
        systemConfigApi.getSchema(),
        systemConfigApi.getConfig(true),
      ]);
      setSchema(schemaRes);
      setConfig(configRes);
      const values: Record<string, string> = {};
      for (const item of configRes.items) {
        values[item.key] = item.value ?? '';
      }
      reset({ ...values, pasteText: '' });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to load configuration';
      setLoadError(msg);
    } finally {
      setLoading(false);
    }
  }, [reset]);

  useEffect(() => {
    void fetchConfig();
  }, [fetchConfig]);

  const aiModelCategory = useMemo<SystemConfigCategorySchema | null>(() => {
    if (!schema) return null;
    return schema.categories.find((c) => c.category === 'ai_model') ?? null;
  }, [schema]);

  const fieldSchemas = useMemo<Record<string, SystemConfigFieldSchema>>(() => {
    if (!aiModelCategory) return {};
    const map: Record<string, SystemConfigFieldSchema> = {};
    for (const field of aiModelCategory.fields) {
      map[field.key] = field;
    }
    return map;
  }, [aiModelCategory]);

  const orderedFields = useMemo<SystemConfigFieldSchema[]>(() => {
    return TARGET_KEYS.map((key) => fieldSchemas[key]).filter(
      (schemaEntry): schemaEntry is SystemConfigFieldSchema => Boolean(schemaEntry),
    );
  }, [fieldSchemas]);

  const maskedKeys = useMemo<Set<string>>(() => {
    if (!config) return new Set();
    const set = new Set<string>();
    for (const item of config.items) {
      if (item.isMasked) set.add(item.key);
    }
    return set;
  }, [config]);

  const dirtyKeys = useMemo<string[]>(() => {
    if (!config) return [];
    const originalValues: Record<string, string> = {};
    for (const item of config.items) {
      originalValues[item.key] = item.rawValueExists ? item.value : '';
    }
    const changed: string[] = [];
    for (const key of TARGET_KEYS) {
      const val = fieldValues[key] ?? '';
      const orig = originalValues[key] ?? '';
      if (val !== orig) changed.push(key);
    }
    return changed;
  }, [config, fieldValues]);

  const handleSave = useCallback(async (submittedValues: ModelFormValues) => {
    if (!config || dirtyKeys.length === 0) return;

    setSaving(true);
    setSaveStatus(null);

    try {
      const items = dirtyKeys.map((key) => ({
        key,
        value: submittedValues[key] ?? '',
      }));

      const result = await systemConfigApi.update({
        configVersion: config.configVersion,
        maskToken: config.maskToken,
        items,
        reloadNow: true,
      });

      setSaveStatus({
        type: 'success',
        message: `已保存 ${result.appliedCount} 项配置（${result.updatedKeys.join(', ')}）。配置已重新加载。`,
      });

      const newConfig = await systemConfigApi.getConfig(true);
      setConfig(newConfig);
      const values: Record<string, string> = { ...submittedValues };
      for (const item of newConfig.items) {
        if (!dirtyKeys.includes(item.key)) {
          values[item.key] = item.value ?? '';
        }
      }
      reset(values);
    } catch (err: unknown) {
      if (err instanceof SystemConfigValidationError) {
        const fieldIssues = err.issues.map((i) => `${i.key}: ${i.message}`).join('\n');
        setSaveStatus({ type: 'error', message: `校验失败:\n${fieldIssues}` });
      } else if (err instanceof SystemConfigConflictError) {
        setSaveStatus({
          type: 'error',
          message: '配置已在别处修改，请刷新页面后重试。',
        });
      } else {
        const msg = err instanceof Error ? err.message : '保存失败';
        setSaveStatus({ type: 'error', message: msg });
      }
    } finally {
      setSaving(false);
    }
  }, [config, dirtyKeys, reset]);

  if (loading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-cyan" />
          <p className="text-sm text-secondary-text">加载模型配置...</p>
        </div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="space-y-4">
        <InlineAlert variant="danger" title="加载失败" message={loadError} />
        <Button variant="secondary" onClick={fetchConfig}>
          <RotateCcw className="h-4 w-4" />
          重试
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit(handleSave)}>
      <section className="space-y-5">
      <header className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-primary/10 text-primary">
            <span className="font-mono text-sm font-semibold">M</span>
          </span>
          <h2 className="text-xl font-semibold text-foreground">模型设置</h2>
        </div>
        <Button
          variant="secondary"
          type="submit"
          disabled={saving || dirtyKeys.length === 0}
          isLoading={saving}
          loadingText="保存中..."
        >
          <Save className="h-4 w-4" />
          保存
        </Button>
      </header>

      <details
        className="terminal-card rounded-2xl"
        open={pasteOpen}
        onToggle={(e) => setPasteOpen((e.currentTarget as HTMLDetailsElement).open)}
      >
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-2">
            <ClipboardPaste className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium text-foreground">JSON</span>
          </div>
          <ChevronDown
            className={
              'h-4 w-4 text-muted-foreground transition-transform ' +
              (pasteOpen ? 'rotate-180' : 'rotate-0')
            }
          />
        </summary>
        <div className="space-y-3 border-t border-border/40 px-4 py-3">
          <Controller
            name="pasteText"
            control={control}
            render={({ field }) => (
              <textarea
                {...field}
                onChange={(e) => handlePasteChange(e.target.value)}
                rows={5}
                className="input-surface w-full rounded-xl border border-border/55 bg-elevated/40 px-3 py-2 font-mono text-xs leading-relaxed transition focus:border-cyan/40 focus:outline-none"
                spellCheck={false}
              />
            )}
          />
        </div>
      </details>

      {saveStatus ? (
        <InlineAlert
          variant={saveStatus.type === 'success' ? 'success' : 'danger'}
          title={saveStatus.type === 'success' ? '保存成功' : '保存失败'}
          message={<pre className="whitespace-pre-wrap font-sans">{saveStatus.message}</pre>}
        />
      ) : null}

      <div className="terminal-card space-y-5 rounded-2xl p-5">
        {orderedFields.length === 0 ? (
          <InlineAlert
            variant="warning"
            title="未找到模型字段"
            message="schema 中没有这些字段，请确认后端 registry 配置。"
          />
        ) : (
          orderedFields.map((fieldSchema) => (
            <Controller
              key={fieldSchema.key}
              name={fieldSchema.key}
              control={control}
              render={({ field }) => (
                <SettingsField
                  field={fieldSchema}
                  value={field.value ?? ''}
                  onChange={(_, value) => {
                    field.onChange(value);
                    setSaveStatus(null);
                  }}
                  isMasked={maskedKeys.has(fieldSchema.key)}
                />
              )}
            />
          ))
        )}
      </div>
      </section>
    </form>
  );
};

export default ModelSettingsView;
