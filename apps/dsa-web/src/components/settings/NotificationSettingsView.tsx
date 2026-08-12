import { useCallback, useEffect, useMemo, useState } from 'react';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { Bell, Loader2, Save, Send } from 'lucide-react';
import { Button, InlineAlert } from '../common';
import { SettingsField } from './SettingsField';
import {
  SystemConfigConflictError,
  SystemConfigValidationError,
  systemConfigApi,
} from '../../api/systemConfig';
import type { SystemConfigFieldSchema, SystemConfigResponse, SystemConfigSchemaResponse } from '../../types/systemConfig';

type Notice = { type: 'success' | 'error'; message: string } | null;
const WECHAT_WEBHOOK_KEY = 'WECHAT_WEBHOOK_URL';

export function NotificationSettingsView() {
  const [schema, setSchema] = useState<SystemConfigSchemaResponse | null>(null);
  const [config, setConfig] = useState<SystemConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const { control, handleSubmit, reset } = useForm<Record<string, string>>({
    defaultValues: {},
  });
  const watchedValues = useWatch({ control });
  const values = useMemo(() => watchedValues ?? {}, [watchedValues]);

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
      reset(Object.fromEntries(configResult.items.map((item) => [item.key, item.value ?? ''])));
    } catch (error) {
      setNotice({ type: 'error', message: error instanceof Error ? error.message : '通知配置加载失败' });
    } finally {
      setLoading(false);
    }
  }, [reset]);

  useEffect(() => {
    void load();
  }, [load]);

  const fields = useMemo<SystemConfigFieldSchema[]>(() => (
    schema?.categories
      .find((category) => category.category === 'notification')
      ?.fields.filter((field) => field.key === WECHAT_WEBHOOK_KEY) ?? []
  ), [schema]);
  const masked = useMemo(() => new Set(
    config?.items.filter((item) => item.isMasked).map((item) => item.key) ?? [],
  ), [config]);
  const dirtyKeys = useMemo(() => {
    if (!config) return [];
    const original = Object.fromEntries(config.items.map((item) => [item.key, item.value ?? '']));
    return fields.map((field) => field.key).filter((key) => (values[key] ?? '') !== (original[key] ?? ''));
  }, [config, fields, values]);

  const save = useCallback(async (submittedValues: Record<string, string>) => {
    if (!config || dirtyKeys.length === 0) return;
    setSaving(true);
    setNotice(null);
    try {
      const result = await systemConfigApi.update({
        configVersion: config.configVersion,
        maskToken: config.maskToken,
        reloadNow: true,
        items: dirtyKeys.map((key) => ({ key, value: submittedValues[key] ?? '' })),
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
  }, [config, dirtyKeys, load]);

  const test = useCallback(async (submittedValues: Record<string, string>) => {
    if (!config) return;
    setTesting(true);
    setNotice(null);
    try {
      const result = await systemConfigApi.testNotificationChannel({
        channel: 'wechat',
        items: fields.map((field) => ({ key: field.key, value: submittedValues[field.key] ?? '' })),
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
  }, [config, fields]);

  if (loading) return <div className="flex min-h-[40vh] items-center justify-center"><Loader2 className="size-8 animate-spin text-primary" /></div>;

  return (
    <form onSubmit={handleSubmit(save)}>
      <section className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="flex size-9 items-center justify-center rounded-md bg-primary/10 text-primary"><Bell className="size-4" /></span>
          <div><h2 className="text-xl font-semibold text-foreground">企业微信通知</h2><p className="mt-0.5 text-xs text-muted-foreground">粘贴群机器人的 Webhook URL 即可接收通知。</p></div>
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="secondary" onClick={() => { void handleSubmit(test)(); }} disabled={testing} isLoading={testing} loadingText="测试中..."><Send className="size-4" />测试通知</Button>
          <Button type="submit" variant="secondary" disabled={saving || dirtyKeys.length === 0} isLoading={saving} loadingText="保存中..."><Save className="size-4" />保存</Button>
        </div>
      </header>
      {notice && <InlineAlert variant={notice.type === 'success' ? 'success' : 'danger'} title={notice.type === 'success' ? '操作成功' : '操作失败'} message={<span className="whitespace-pre-wrap">{notice.message}</span>} />}
      {fields.length === 0 ? (
        <InlineAlert variant="warning" title="暂时无法配置" message="后端没有返回企业微信 Webhook 配置。" />
      ) : (
        <div className="terminal-card rounded-2xl p-5">
          {fields.map((field) => (
            <Controller
              key={field.key}
              name={field.key}
              control={control}
              render={({ field: controllerField }) => (
                <SettingsField
                  field={field}
                  value={controllerField.value ?? ''}
                  onChange={(_, value) => {
                    controllerField.onChange(value);
                    setNotice(null);
                  }}
                  isMasked={masked.has(field.key)}
                  placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."
                />
              )}
            />
          ))}
        </div>
      )}
      </section>
    </form>
  );
}

export default NotificationSettingsView;
