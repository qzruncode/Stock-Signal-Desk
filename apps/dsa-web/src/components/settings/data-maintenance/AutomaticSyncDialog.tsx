import { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button, FormCheckbox, InlineAlert, Input, Modal, Select } from '../../common';
import { dataMaintenanceApi as api, type DatasetId, type DatasetOverview, type DatasetSummary, type SyncPolicy } from '../../../api/dataMaintenance';
import { datasetNames, formatDate, messageOf } from './presentation';

type PolicyFormValues = { enabled: boolean; intervalMinutes: number; maxAgeMinutes: number };
const formValues = (policy: SyncPolicy): PolicyFormValues => ({ enabled: policy.enabled, intervalMinutes: policy.interval_seconds / 60, maxAgeMinutes: policy.max_age_seconds / 60 });

function PolicyForm({ dataset, connected, onBusy }: { dataset: DatasetSummary; connected: boolean; onBusy: (busy: boolean) => void }) {
  const cache = useQueryClient();
  const form = useForm<PolicyFormValues>({ defaultValues: formValues(dataset.policy), mode: 'onChange' });
  // Read all proxy-backed state during render, not behind a short-circuit expression.
  const { errors, isDirty, isValid } = form.formState;
  const mutation = useMutation({
    mutationFn: (value: PolicyFormValues) => api.policy(dataset.id, {
      enabled: value.enabled, interval_seconds: Math.round(value.intervalMinutes * 60), max_age_seconds: Math.round(value.maxAgeMinutes * 60),
    }),
    onMutate: () => onBusy(true),
    onSuccess: policy => {
      cache.setQueryData<DatasetOverview>(['data-service', 'overview'], previous => previous ? {
        ...previous, items: previous.items.map(item => item.id === dataset.id ? { ...item, policy } : item),
      } : previous);
      form.reset(formValues(policy));
    },
    onSettled: () => onBusy(false),
  });
  const validMinutes = (value: number) => Number.isFinite(value) && Math.abs(value * 60 - Math.round(value * 60)) < 1e-6 || '请输入精确到秒的有效时长';
  return <form className="space-y-4" noValidate onSubmit={form.handleSubmit(value => mutation.mutate(value))}>
    <Controller control={form.control} name="enabled" render={({ field }) => <FormCheckbox label="启用自动同步" checked={field.value} onChange={field.onChange} disabled={!connected || mutation.isPending} />} />
    <div className="grid gap-4 sm:grid-cols-2">
      <Input type="number" density="compact" label="同步间隔（分钟）" min={0.5} max={10080} step="any" disabled={!connected || mutation.isPending}
        error={errors.intervalMinutes?.message} {...form.register('intervalMinutes', {
          valueAsNumber: true, required: '请输入同步间隔', min: { value: 0.5, message: '最短 0.5 分钟' }, max: { value: 10080, message: '最长 7 天' }, validate: validMinutes, deps: ['maxAgeMinutes'],
        })} />
      <Input type="number" density="compact" label="允许延迟（分钟）" min={0.5} max={20160} step="any" disabled={!connected || mutation.isPending}
        error={errors.maxAgeMinutes?.message} {...form.register('maxAgeMinutes', {
          valueAsNumber: true, required: '请输入允许延迟', min: { value: 0.5, message: '最短 0.5 分钟' }, max: { value: 20160, message: '最长 14 天' },
          validate: { duration: validMinutes, interval: (value, values) => value >= values.intervalMinutes || '允许延迟不能小于同步间隔' },
        })} />
    </div>
    <p className="text-xs leading-5 text-muted-foreground">暂停只停止后续自动计划，不取消正在执行的任务；业务首次读取仍可触发补采。</p>
    {dataset.scope === 'subscribed' && <p className="text-xs text-muted-foreground">按近期使用的订阅维护，当前 {dataset.total} 项订阅。</p>}
    <p className="text-xs text-muted-foreground">{dataset.policy.enabled ? '下次计划：' + formatDate(dataset.policy.next_run_at) : '当前自动计划已暂停'}</p>
    {mutation.isError && <InlineAlert variant="danger" message={messageOf(mutation.error)} />}
    {mutation.isSuccess && !isDirty && <p role="status" className="text-xs text-success">自动同步设置已保存</p>}
    <div className="flex justify-end border-t border-border/60 pt-3"><Button type="submit" size="sm" isLoading={mutation.isPending}
      disabled={!connected || !isDirty || !isValid}>保存设置</Button></div>
  </form>;
}

export function AutomaticSyncDialog({ datasets, connected, onClose }: { datasets: DatasetSummary[]; connected: boolean; onClose: () => void }) {
  const [selected, setSelected] = useState<DatasetId>(datasets.find(item => item.id === 'kline')?.id || datasets[0].id);
  const [busy, setBusy] = useState(false);
  const dataset = datasets.find(item => item.id === selected)!;
  return <Modal isOpen title="自动同步设置" onClose={onClose} width="max-w-xl" preventClose={busy}>
    <div className="space-y-4">
      {!connected && <InlineAlert variant="warning" message="连接中断，恢复后可保存设置。" />}
      <Select density="compact" ariaLabel="选择自动同步数据集" value={selected} disabled={busy}
        options={datasets.map(item => ({ value: item.id, label: datasetNames[item.id] }))} onChange={value => setSelected(value as DatasetId)} />
      <PolicyForm key={dataset.id} dataset={dataset} connected={connected} onBusy={setBusy} />
    </div>
  </Modal>;
}
