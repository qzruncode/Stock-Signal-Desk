import { useCallback, useEffect, useMemo, useState } from 'react';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { ChevronDown, Info, ClipboardPaste, Loader2, PlugZap, RotateCcw, Save } from 'lucide-react';
import { Button, InlineAlert, Tooltip } from '../common';
import { Textarea } from '../ui/textarea';
import { useToast } from '../common/ToastContext';
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
  const { toast } = useToast();
  const [schema, setSchema] = useState<SystemConfigSchemaResponse | null>(null);
  const [config, setConfig] = useState<SystemConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testingCapability, setTestingCapability] = useState<'chat' | null>(null);
  const { control, getValues, handleSubmit, reset, setValue } = useForm<ModelFormValues>({
    defaultValues: { pasteText: '' },
  });
  const watchedFieldValues = useWatch({ control });
  const fieldValues = useMemo(() => watchedFieldValues ?? {}, [watchedFieldValues]);

  const [pasteOpen, setPasteOpen] = useState(false);

  // 表单 -> JSON：fieldValues 变化时把模型字段拼成 JSON 回显
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
  }, [setValue]);

  const fetchConfig = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [schemaRes, configRes] = await Promise.all([
        systemConfigApi.getSchema(),
        systemConfigApi.getConfig(true, true),
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
      toast({ title: '模型配置加载失败', description: msg, variant: 'error' });
    } finally {
      setLoading(false);
    }
  }, [reset, toast]);

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

      toast({
        title: '保存成功',
        description: '已保存 ' + result.appliedCount + ' 项配置（' + result.updatedKeys.join(', ') + '）。配置已重新加载。',
        variant: 'success',
      });

      const newConfig = await systemConfigApi.getConfig(true, true);
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
        toast({ title: '保存失败', description: '校验失败:\n' + fieldIssues, variant: 'error' });
      } else if (err instanceof SystemConfigConflictError) {
        toast({
          title: '保存失败',
          description: '配置已在别处修改，请刷新页面后重试。',
          variant: 'error',
        });
      } else {
        const msg = err instanceof Error ? err.message : '保存失败';
        toast({ title: '保存失败', description: msg, variant: 'error' });
      }
    } finally {
      setSaving(false);
    }
  }, [config, dirtyKeys, reset, toast]);

  const handleTest = useCallback(async (submittedValues: ModelFormValues) => {
    if (!config) return;

    setTesting(true);
    setTestingCapability('chat');
    try {
      const result = await systemConfigApi.testModelConnection({
        items: TARGET_KEYS.map((key) => ({ key, value: submittedValues[key] ?? '' })),
        maskToken: config.maskToken,
      });
      toast({
        title: result.success ? '测试成功' : '测试失败',
        description: result.message,
        variant: result.success ? 'success' : 'error',
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '模型测试失败';
      toast({ title: '测试失败', description: msg, variant: 'error' });
    } finally {
      setTesting(false);
      setTestingCapability(null);
    }
  }, [config, toast]);

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
      <section className="space-y-6">
        <header className="flex items-end justify-between gap-4 border-b border-border/60 pb-4">
          <div className="min-w-0">
            <p className="mb-1 text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
              运行时配置
            </p>
            <h2 className="text-lg font-semibold tracking-tight text-foreground">模型设置</h2>
            <p className="mt-1 text-xs text-secondary-text">
              配置聊天模型网关与生成参数；PDF 知识库使用独立部署的开源检索模型。
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
            <Button
              variant="ghost"
              size="sm"
              className="h-8 rounded-md border border-border/70 px-2.5 text-xs"
              type="button"
              onClick={() => { void handleSubmit(handleTest)(); }}
              disabled={saving || testing}
              isLoading={testingCapability === 'chat'}
              loadingText="测试中"
            >
              <PlugZap className="h-3.5 w-3.5" />
              测试聊天
            </Button>
            <Button
              variant="secondary"
              size="sm"
              className="h-8 rounded-md border-foreground/15 bg-foreground px-3 text-xs text-background hover:bg-foreground/90 hover:text-background"
              type="submit"
              disabled={saving || testing || dirtyKeys.length === 0}
              isLoading={saving}
              loadingText="保存中"
            >
              <Save className="h-3.5 w-3.5" />
              保存
            </Button>
          </div>
        </header>

        <details
          className="group border-y border-border/60"
          open={pasteOpen}
          onToggle={(e) => setPasteOpen((e.currentTarget as HTMLDetailsElement).open)}
        >
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 py-2.5 text-xs text-secondary-text">
            <span className="flex items-center gap-2">
              <ClipboardPaste className="h-3.5 w-3.5" />
              <span className="font-medium text-foreground">原始配置</span>
              <span className="font-mono text-[10px] text-muted-foreground">JSON</span>
            </span>
            <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              {pasteOpen ? '收起' : '展开'}
              <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
            </span>
          </summary>
          <div className="border-t border-border/50 py-3">
            <Controller
              name="pasteText"
              control={control}
              render={({ field }) => (
                <Textarea
                  {...field}
                  onChange={(e) => handlePasteChange(e.target.value)}
                  rows={4}
                  className="input-surface w-full bg-background/40 px-3 py-2 font-mono text-[11px] leading-5 shadow-none transition focus:border-foreground/30"
                  spellCheck={false}
                  aria-label="原始模型配置 JSON"
                />
              )}
            />
          </div>
        </details>

        {orderedFields.length === 0 ? (
          <InlineAlert
            variant="warning"
            title="未找到模型字段"
            message="schema 中没有这些字段，请确认后端 registry 配置。"
          />
        ) : (
          <div className="divide-y divide-border/50 border-y border-border/60">
            {orderedFields.map((fieldSchema) => (
              <div
                key={fieldSchema.key}
                className="grid grid-cols-1 gap-2.5 py-3.5 sm:grid-cols-[minmax(12rem,0.42fr)_minmax(0,1fr)] sm:items-start sm:gap-8"
              >
                <div className="min-w-0 pt-0.5">
                  <div className="flex items-center gap-1.5">
                    <p className="text-[13px] font-medium leading-5 text-foreground">
                      {fieldSchema.title ?? fieldSchema.key}
                    </p>
                    <Tooltip
                      focusable
                      ariaLabel={'查看 ' + (fieldSchema.title ?? fieldSchema.key) + ' 配置说明'}
                      content={
                        <div className="space-y-1 whitespace-normal">
                          <p className="font-mono text-[10px] text-muted-foreground">{fieldSchema.key}</p>
                          {fieldSchema.description ? <p>{fieldSchema.description}</p> : null}
                          {fieldSchema.examples?.length ? (
                            <p className="text-muted-foreground">例：{fieldSchema.examples.join('、')}</p>
                          ) : null}
                        </div>
                      }
                      contentClassName="min-w-0 max-w-[20rem] whitespace-normal"
                    >
                      <Info className="size-3.5 cursor-help text-muted-foreground transition-colors hover:text-primary" aria-hidden="true" />
                    </Tooltip>
                  </div>
                </div>
                <Controller
                  name={fieldSchema.key}
                  control={control}
                  render={({ field }) => (
                    <SettingsField
                      field={fieldSchema}
                      value={field.value ?? ''}
                      onChange={(_, value) => {
                        field.onChange(value);
                      }}
                      isMasked={maskedKeys.has(fieldSchema.key)}
                      showSensitiveValue
                      compact
                      showLabel={false}
                      showHint={false}
                    />
                  )}
                />
              </div>
            ))}
          </div>
        )}
      </section>
    </form>
  );
};

export default ModelSettingsView;
