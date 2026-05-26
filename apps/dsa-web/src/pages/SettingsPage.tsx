import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Save, RotateCcw, Loader2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import { systemConfigApi, SystemConfigValidationError, SystemConfigConflictError } from '../api/systemConfig';
import { Button, InlineAlert } from '../components/common';
import { ModelConfigSection } from '../components/settings/ModelConfigSection';
import type {
  SystemConfigSchemaResponse,
  SystemConfigResponse,
  SystemConfigFieldSchema,
  SystemConfigCategorySchema,
} from '../types/systemConfig';

type SaveStatus = { type: 'success' | 'error'; message: string } | null;

const SettingsPage: React.FC = () => {
  const [schema, setSchema] = useState<SystemConfigSchemaResponse | null>(null);
  const [config, setConfig] = useState<SystemConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>(null);

  // Local form values keyed by field key
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});

  useEffect(() => {
    document.title = '模型 API 配置 - Stock-Signal-Desk';
  }, []);

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

      // Initialize local field values from config items
      const values: Record<string, string> = {};
      for (const item of configRes.items) {
        values[item.key] = item.value ?? '';
      }
      setFieldValues(values);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to load configuration';
      setLoadError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const aiModelCategory = useMemo<SystemConfigCategorySchema | null>(() => {
    if (!schema) return null;
    return schema.categories.find((c) => c.category === 'ai_model') ?? null;
  }, [schema]);

  // Build a lookup of field schema by key
  const fieldSchemas = useMemo<Record<string, SystemConfigFieldSchema>>(() => {
    if (!aiModelCategory) return {};
    const map: Record<string, SystemConfigFieldSchema> = {};
    for (const field of aiModelCategory.fields) {
      map[field.key] = field;
    }
    return map;
  }, [aiModelCategory]);

  // Set of keys whose values are masked
  const maskedKeys = useMemo<Set<string>>(() => {
    if (!config) return new Set();
    const set = new Set<string>();
    for (const item of config.items) {
      if (item.isMasked) set.add(item.key);
    }
    return set;
  }, [config]);

  // Track dirty state
  const dirtyKeys = useMemo<string[]>(() => {
    if (!config) return [];
    const originalValues: Record<string, string> = {};
    for (const item of config.items) {
      originalValues[item.key] = item.rawValueExists ? item.value : '';
    }
    const changed: string[] = [];
    for (const [key, val] of Object.entries(fieldValues)) {
      const orig = originalValues[key] ?? '';
      if (val !== orig) changed.push(key);
    }
    return changed;
  }, [config, fieldValues]);

  const handleFieldChange = useCallback((key: string, value: string) => {
    setFieldValues((prev) => ({ ...prev, [key]: value }));
    setSaveStatus(null);
  }, []);

  const handleSave = useCallback(async () => {
    if (!config || dirtyKeys.length === 0) return;

    setSaving(true);
    setSaveStatus(null);

    try {
      const items = dirtyKeys.map((key) => ({
        key,
        value: fieldValues[key] ?? '',
      }));

      const result = await systemConfigApi.update({
        configVersion: config.configVersion,
        maskToken: config.maskToken,
        items,
        reloadNow: true,
      });

      setSaveStatus({
        type: 'success',
        message: `已保存 ${result.appliedCount} 项配置 (${result.updatedKeys.join(', ')})。配置已重新加载。`,
      });

      // Refetch config to get new version and updated values
      const newConfig = await systemConfigApi.getConfig(true);
      setConfig(newConfig);
      const values: Record<string, string> = { ...fieldValues };
      for (const item of newConfig.items) {
        if (!dirtyKeys.includes(item.key)) {
          values[item.key] = item.value ?? '';
        }
      }
      setFieldValues(values);
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
  }, [config, dirtyKeys, fieldValues]);

  const handleReset = useCallback(() => {
    if (!config) return;
    const values: Record<string, string> = {};
    for (const item of config.items) {
      values[item.key] = item.value ?? '';
    }
    setFieldValues(values);
    setSaveStatus(null);
  }, [config]);

  // --- Render states ---

  if (loading) {
    return (
      <div className="settings-page flex min-h-[60vh] items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-cyan" />
          <p className="text-sm text-secondary-text">加载模型配置...</p>
        </div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="settings-page space-y-4">
        <InlineAlert variant="danger" title="加载失败" message={loadError} />
        <Button variant="settings-secondary" onClick={fetchConfig}>
          <RotateCcw className="h-4 w-4" />
          重试
        </Button>
      </div>
    );
  }

  return (
    <div className="settings-page mx-auto max-w-3xl py-6">
      {/* Header */}
      <div className="mb-6">
        <Link
          to="/"
          className="mb-3 inline-flex items-center gap-1 text-sm text-secondary-text transition hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          返回首页
        </Link>
        <h1 className="text-2xl font-semibold text-foreground">模型 API 配置</h1>
        <p className="mt-1 text-sm text-secondary-text">
          填入 API Key，设置主模型即可使用。模型名必须用 <code className="rounded bg-cyan/10 px-1 py-0.5 text-xs text-cyan">provider/model</code> 格式。
        </p>
      </div>

      {/* Save status */}
      {saveStatus && (
        <div className="mb-4">
          <InlineAlert
            variant={saveStatus.type === 'success' ? 'success' : 'danger'}
            title={saveStatus.type === 'success' ? '保存成功' : '保存失败'}
            message={<pre className="whitespace-pre-wrap font-sans">{saveStatus.message}</pre>}
          />
        </div>
      )}

      {/* Model config cards */}
      {aiModelCategory && (
        <ModelConfigSection
          fieldValues={fieldValues}
          fieldSchemas={fieldSchemas}
          maskedKeys={maskedKeys}
          onChange={handleFieldChange}
        />
      )}

      {/* Fallback when schema is loaded but ai_model category is missing */}
      {schema && !aiModelCategory && (
        <InlineAlert
          variant="warning"
          title="无模型配置"
          message="未找到 AI 模型配置分类，请确认后端 schema 是否正确加载。"
        />
      )}

      {/* Sticky footer */}
      {dirtyKeys.length > 0 && (
        <div className="settings-surface-strong sticky bottom-4 z-20 mt-6 flex items-center gap-3 rounded-xl border border-warning/30 p-4 shadow-[0_-8px_32px_rgba(15,23,42,0.18)] backdrop-blur-xl">
          <span className="flex-1 text-sm text-secondary-text">
            有 {dirtyKeys.length} 项更改未保存
          </span>
          <Button variant="settings-secondary" size="sm" onClick={handleReset} disabled={saving}>
            <RotateCcw className="h-4 w-4" />
            放弃更改
          </Button>
          <Button variant="settings-primary" size="sm" onClick={handleSave} isLoading={saving} loadingText="保存中...">
            <Save className="h-4 w-4" />
            保存配置
          </Button>
        </div>
      )}
    </div>
  );
};

export default SettingsPage;
