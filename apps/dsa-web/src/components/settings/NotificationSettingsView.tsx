import { useCallback, useEffect, useMemo, useState } from 'react';
import { Bell, Loader2, RotateCcw, Save, Send } from 'lucide-react';
import { Button, InlineAlert } from '../common';
import { SettingsField } from './SettingsField';
import {
  SystemConfigConflictError,
  SystemConfigValidationError,
  systemConfigApi,
} from '../../api/systemConfig';
import type { SystemConfigFieldSchema, SystemConfigResponse, SystemConfigSchemaResponse } from '../../types/systemConfig';

type Notice = { type: 'success' | 'error'; message: string } | null;

export function NotificationSettingsView() {
  const [schema, setSchema] = useState<SystemConfigSchemaResponse | null>(null);
  const [config, setConfig] = useState<SystemConfigResponse | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setNotice(null);
    try {
      const [schemaResult, configResult] = await Promise.all([
        systemConfigApi.getSchema(),
        systemConfigApi.getConfig(true),
      ]);
      setSchema(schemaResult);
      setConfig(configResult);
      setValues(Object.fromEntries(configResult.items.map((item) => [item.key, item.value ?? ''])));
    } catch (error) {
      setNotice({ type: 'error', message: error instanceof Error ? error.message : '通知配置加载失败' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- settings are initialized from the persisted API config
    void load();
  }, [load]);

  const fields = useMemo<SystemConfigFieldSchema[]>(() => (
    schema?.categories.find((category) => category.category === 'notification')?.fields ?? []
  ), [schema]);
  const masked = useMemo(() => new Set(
    config?.items.filter((item) => item.isMasked).map((item) => item.key) ?? [],
  ), [config]);
  const dirtyKeys = useMemo(() => {
    if (!config) return [];
    const original = Object.fromEntries(config.items.map((item) => [item.key, item.value ?? '']));
    return fields.map((field) => field.key).filter((key) => (values[key] ?? '') !== (original[key] ?? ''));
  }, [config, fields, values]);

  const save = useCallback(async () => {
    if (!config || dirtyKeys.length === 0) return;
    setSaving(true);
    setNotice(null);
    try {
      const result = await systemConfigApi.update({
        configVersion: config.configVersion,
        maskToken: config.maskToken,
        reloadNow: true,
        items: dirtyKeys.map((key) => ({ key, value: values[key] ?? '' })),
      });
      setNotice({ type: 'success', message: `已保存 ${result.appliedCount} 项通知配置。` });
      await load();
    } catch (error) {
      if (error instanceof SystemConfigValidationError) {
        setNotice({ type: 'error', message: error.issues.map((item) => `${item.key}: ${item.message}`).join('\n') });
      } else if (error instanceof SystemConfigConflictError) {
        setNotice({ type: 'error', message: '配置已在其他位置修改，请刷新后重试。' });
      } else {
        setNotice({ type: 'error', message: error instanceof Error ? error.message : '保存失败' });
      }
    } finally {
      setSaving(false);
    }
  }, [config, dirtyKeys, load, values]);

  const test = useCallback(async () => {
    if (!config) return;
    setTesting(true);
    setNotice(null);
    try {
      const result = await systemConfigApi.testNotificationChannel({
        channel: 'wechat',
        items: fields.map((field) => ({ key: field.key, value: values[field.key] ?? '' })),
        maskToken: config.maskToken,
        title: 'Stock Assistant 通知测试',
        content: '通知渠道已连接，AI 助手可以在你明确要求时发送分析结果。',
      });
      setNotice({ type: result.success ? 'success' : 'error', message: result.message });
    } catch (error) {
      setNotice({ type: 'error', message: error instanceof Error ? error.message : '通知测试失败' });
    } finally {
      setTesting(false);
    }
  }, [config, fields, values]);

  if (loading) return <div className="flex min-h-[40vh] items-center justify-center"><Loader2 className="size-8 animate-spin text-primary" /></div>;

  return (
    <section className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="flex size-9 items-center justify-center rounded-md bg-primary/10 text-primary"><Bell className="size-4" /></span>
          <div><h2 className="text-xl font-semibold text-foreground">通知设置</h2><p className="mt-0.5 text-xs text-muted-foreground">助手只会在你明确要求时发送通知。</p></div>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={test} disabled={testing} isLoading={testing} loadingText="测试中..."><Send className="size-4" />测试通知</Button>
          <Button variant="secondary" onClick={save} disabled={saving || dirtyKeys.length === 0} isLoading={saving} loadingText="保存中..."><Save className="size-4" />保存</Button>
        </div>
      </header>
      {notice && <InlineAlert variant={notice.type === 'success' ? 'success' : 'danger'} title={notice.type === 'success' ? '操作成功' : '操作失败'} message={<span className="whitespace-pre-wrap">{notice.message}</span>} />}
      {fields.length === 0 ? (
        <InlineAlert variant="warning" title="暂无通知配置" message="后端没有返回通知渠道配置字段。" />
      ) : (
        <div className="terminal-card space-y-5 rounded-2xl p-5">
          {fields.map((field) => <SettingsField key={field.key} field={field} value={values[field.key] ?? ''} onChange={(key, value) => { setValues((current) => ({ ...current, [key]: value })); setNotice(null); }} isMasked={masked.has(field.key)} />)}
        </div>
      )}
      <Button variant="ghost" onClick={load}><RotateCcw className="size-4" />重新加载</Button>
    </section>
  );
}
