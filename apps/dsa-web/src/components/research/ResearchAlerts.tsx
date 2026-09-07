import { useState } from 'react';
import { useController, useForm, useWatch } from 'react-hook-form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { researchApi, type ResearchAlertInput } from '../../api/research';
import { toApiErrorMessage } from '../../api/error';
import { watchlistApi } from '../../api/watchlist';

const defaults: ResearchAlertInput = {
  name: '',
  target: '',
  target_scope: 'single_symbol',
  enabled: false,
  notification_enabled: false,
  parameters: {
    kinds: ['news', 'financial', 'research'],
    interval_seconds: 900,
    cooldown_seconds: 3600,
    below_price: null,
  },
};

export function ResearchAlerts() {
  const client = useQueryClient();
  const form = useForm<ResearchAlertInput>({ defaultValues: defaults });
  const [editingId, setEditingId] = useState<number | undefined>();
  const scope = useWatch({ control: form.control, name: 'target_scope' });
  const { field: targetField } = useController({ control: form.control, name: 'target', rules: { required: true } });
  const groups = useQuery({
    queryKey: ['watchlist-groups'],
    queryFn: watchlistApi.listGroups,
    enabled: scope === 'watchlist_group',
  });
  const rules = useQuery({ queryKey: ['research-alerts'], queryFn: ({ signal }) => researchApi.alerts(signal) });
  const history = useQuery({
    queryKey: ['research-alert-history'],
    queryFn: ({ signal }) => researchApi.history(signal),
    refetchInterval: 60_000,
  });
  const save = useMutation({
    mutationFn: ({ input, id }: { input: ResearchAlertInput; id?: number }) => researchApi.saveAlert(input, id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['research-alerts'] });
    },
  });
  const check = useMutation({
    mutationFn: researchApi.checkAlert,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['research-alerts'] });
      void client.invalidateQueries({ queryKey: ['research-alert-history'] });
    },
  });
  const error = save.error || check.error || rules.error || history.error || groups.error;
  return (
    <section aria-label="变化提醒" className="space-y-4 border-t border-border pt-6">
      <h2 className="text-lg font-semibold">变化提醒</h2>
      <p className="text-sm text-muted-foreground">
        跟踪已同步的新闻、财务、研究结论及日线价格条件。首次检查只建立基线；推送需单独开启，不会自动抓取实时行情或下单。
      </p>
      <form
        onSubmit={form.handleSubmit((input) =>
          save.mutate(
            { input, id: editingId },
            {
              onSuccess: () => {
                form.reset(defaults);
                setEditingId(undefined);
              },
            },
          ),
        )}
        className="grid gap-3 rounded-xl border border-border p-4 sm:grid-cols-2"
      >
        <label className="grid gap-1 text-sm">
          提醒名称
          <input
            {...form.register('name', { required: true })}
            required
            maxLength={64}
            className="rounded border border-border bg-background p-2"
          />
        </label>
        <label className="grid gap-1 text-sm">
          目标类型
          <select
            {...form.register('target_scope', { onChange: () => form.setValue('target', '') })}
            className="rounded border border-border bg-background p-2"
          >
            <option value="single_symbol">股票代码</option>
            <option value="watchlist_group">自选分组</option>
          </select>
        </label>
        <label className="grid gap-1 text-sm">
          目标
          {scope === 'watchlist_group' ? (
            <select {...targetField} required className="rounded border border-border bg-background p-2">
              <option value="">选择自选分组</option>
              {groups.data?.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}（{group.codes.length} 只）
                </option>
              ))}
            </select>
          ) : (
            <input
              {...targetField}
              required
              placeholder="例如 600519"
              className="rounded border border-border bg-background p-2"
            />
          )}
        </label>
        <label className="grid gap-1 text-sm">
          日线收盘价跌破（可选）
          <input
            type="number"
            step="any"
            min="0.01"
            {...form.register('parameters.below_price', {
              setValueAs: (value) => (value === '' ? null : Number(value)),
            })}
            className="rounded border border-border bg-background p-2"
          />
        </label>
        <label className="grid gap-1 text-sm">
          检查间隔（秒）
          <input
            type="number"
            min={60}
            max={86400}
            {...form.register('parameters.interval_seconds', { valueAsNumber: true })}
            className="rounded border border-border bg-background p-2"
          />
        </label>
        <label className="grid gap-1 text-sm">
          提醒冷却（秒）
          <input
            type="number"
            min={60}
            max={604800}
            {...form.register('parameters.cooldown_seconds', { valueAsNumber: true })}
            className="rounded border border-border bg-background p-2"
          />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" {...form.register('enabled')} />
          启用变化检查
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" {...form.register('notification_enabled')} />
          允许企业微信自动推送
        </label>
        <fieldset className="flex flex-wrap gap-3 text-sm sm:col-span-2">
          <legend className="mb-2">变化类型</legend>
          {(['news', 'financial', 'research'] as const).map((kind) => (
            <label key={kind} className="flex items-center gap-1">
              <input type="checkbox" value={kind} {...form.register('parameters.kinds')} />
              {{ news: '新闻来源', financial: '财务指标', research: '研究结论' }[kind]}
            </label>
          ))}
        </fieldset>
        <button disabled={save.isPending} className="rounded bg-primary px-3 py-2 text-primary-foreground">
          {editingId ? '更新提醒' : '保存提醒'}
        </button>
        {editingId && (
          <button
            type="button"
            onClick={() => {
              setEditingId(undefined);
              form.reset(defaults);
            }}
          >
            取消编辑
          </button>
        )}
      </form>
      {error && <p role="alert">{toApiErrorMessage(error, '提醒操作失败')}</p>}
      {check.data && (
        <p role="status">
          检查结果：
          {{
            changed: '发现变化，已记录',
            unchanged: '已建立基线或没有变化',
            disabled: '提醒未启用',
            cooldown: '冷却中，变化保留至下次提醒',
          }[check.data.status] || check.data.status}
        </p>
      )}
      {rules.data?.length === 0 && <p className="text-sm text-muted-foreground">暂无提醒，默认不会发送通知。</p>}
      {rules.data?.map((rule) => (
        <div
          key={rule.id}
          className="flex flex-wrap items-center justify-between gap-3 rounded border border-border p-3 text-sm"
        >
          <div>
            <p>
              {rule.name} · {rule.target} · {rule.enabled ? '已启用' : '已暂停'} ·{' '}
              {rule.notification_enabled ? '企业微信推送' : '仅站内记录'}
            </p>
            <p className="text-xs text-muted-foreground">
              最后检查：{rule.state.checked_at || '尚未检查'}
              {rule.state.error && ` · ${rule.state.error}`}
            </p>
          </div>
          <div className="flex gap-3">
            <button
              onClick={() => {
                setEditingId(rule.id);
                form.reset(rule);
              }}
            >
              编辑
            </button>
            <button
              disabled={save.isPending}
              onClick={() => save.mutate({ id: rule.id, input: { ...rule, enabled: !rule.enabled } })}
            >
              {rule.enabled ? '暂停' : '启用'}
            </button>
            <button disabled={!rule.enabled || check.isPending} onClick={() => check.mutate(rule.id)}>
              立即检查
            </button>
          </div>
        </div>
      ))}
      <h3 className="font-medium">变化记录</h3>
      {history.data?.length === 0 && <p className="text-sm text-muted-foreground">尚未记录变化。</p>}
      {history.data?.map((item) => (
        <details key={item.id} className="rounded border border-border p-3 text-sm">
          <summary>
            {item.name} · {item.created_at.replace('T', ' ')} ·{' '}
            {{
              recorded: '站内记录',
              pending: '等待推送',
              sending: '发送中或待确认',
              sent: '已推送',
              delivery_unknown: '发送未确认，不自动重发',
            }[item.status] || item.status}
          </summary>
          {item.changes.map((change, index) => (
            <div key={index} className="mt-3 space-y-2">
              <p>
                {change.symbol} /{' '}
                {{ news: '新来源条目', financial: '财务变化', research: '研究变化', below_price: '价格条件触发' }[
                  change.kind
                ] || change.kind}
              </p>
              <div className="grid gap-2 md:grid-cols-2">
                <pre className="overflow-auto whitespace-pre-wrap break-all rounded bg-muted p-2">
                  变化前：{JSON.stringify(change.before, null, 2)}
                </pre>
                <pre className="overflow-auto whitespace-pre-wrap break-all rounded bg-muted p-2">
                  变化后：{JSON.stringify(change.after, null, 2)}
                </pre>
              </div>
            </div>
          ))}
        </details>
      ))}
    </section>
  );
}
