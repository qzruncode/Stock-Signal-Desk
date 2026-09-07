import { useState } from 'react';
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';
import { ArrowDownToLine, RefreshCw, Search } from 'lucide-react';
import { Badge, Button, EmptyState, InlineAlert, Input, Modal, Select } from '../../common';
import { dataMaintenanceApi as api, type DatasetId, type DatasetSummary, type Freshness } from '../../../api/dataMaintenance';
import { active, datasetNames, formatDate, freshnessNames, liveQuery, messageOf } from './presentation';
import { PageNavigation } from './shared';

export function CoverageDialog({ datasets, connected, canStart, pending, error, onSync, onClose }: {
  datasets: DatasetSummary[]; connected: boolean; canStart: boolean; pending: boolean; error?: string; onSync: (dataset: DatasetId) => void; onClose: () => void;
}) {
  const [filter, setFilter] = useState({ dataset: 'kline' as DatasetId, page: 1, status: 'all', search: '' });
  const [search, setSearch] = useState('');
  const dataset = datasets.find(item => item.id === filter.dataset);
  const query = useQuery({
    queryKey: ['data-service', 'coverage', filter.dataset, filter.page, filter.status, filter.search],
    queryFn: () => api.coverage(filter.dataset, { page: filter.page, status: filter.status, search: filter.search }),
    ...liveQuery,
    placeholderData: (previous, previousQuery) => previousQuery?.queryKey[2] === filter.dataset ? keepPreviousData(previous) : undefined,
  });
  const download = useMutation({ mutationFn: () => api.exportCoverage(filter.dataset) });
  return <Modal isOpen title="数据明细" onClose={onClose} width="max-w-5xl">
    <div className="space-y-3">
      {!connected && <InlineAlert variant="warning" message="当前为上次快照，连接恢复后会更新。" />}
      {error && <InlineAlert variant="danger" message={error} />}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Select density="compact" ariaLabel="选择明细数据集" value={filter.dataset} options={datasets.map(item => ({ value: item.id, label: datasetNames[item.id] }))}
          onChange={value => { setFilter({ dataset: value as DatasetId, page: 1, status: 'all', search: '' }); setSearch(''); download.reset(); }} />
        <Select density="compact" ariaLabel="按数据状态筛选" value={filter.status} options={[{ value: 'all', label: '全部状态' }, ...Object.entries(freshnessNames).map(([value, label]) => ({ value, label }))]}
          onChange={status => setFilter(value => ({ ...value, status, page: 1 }))} />
      </div>
      {dataset && <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>已更新 {dataset.fresh.toLocaleString()} / {dataset.total.toLocaleString()}</span>
        {(['stale', 'missing', 'failed', 'partial', 'unknown'] as Freshness[]).filter(key => dataset[key] > 0).map(key => <span key={key}>{freshnessNames[key]} {dataset[key].toLocaleString()}</span>)}
        {dataset.scope === 'subscribed' && <span>范围：近期使用的订阅</span>}
      </div>}
      <div className="flex flex-wrap gap-2">
        <form className="flex min-w-0 basis-full gap-2 sm:flex-1 sm:basis-auto" onSubmit={event => { event.preventDefault(); setFilter(value => ({ ...value, search: search.trim(), page: 1 })); }}>
          <div className="min-w-0 flex-1"><Input density="compact" aria-label="搜索证券或来源" placeholder="代码、公司或来源" value={search} onChange={event => setSearch(event.target.value)} /></div>
          <Button type="submit" size="sm" variant="secondary"><Search className="size-3.5" />搜索</Button>
        </form>
        <Button size="sm" variant="outline" isLoading={download.isPending} disabled={!connected} onClick={() => download.mutate()}><ArrowDownToLine className="size-3.5" />导出全部</Button>
        <Button size="sm" variant="secondary" disabled={!canStart || pending || !dataset?.total || active(dataset.latest_job)} onClick={() => onSync(filter.dataset)}>更新未达标数据</Button>
        <Button size="sm" variant="ghost" aria-label="刷新数据明细" disabled={query.isFetching} onClick={() => query.refetch()}><RefreshCw className="size-3.5" /></Button>
      </div>
      {download.isError && <InlineAlert variant="danger" message={messageOf(download.error)} />}
      {query.isError ? <InlineAlert variant="danger" message={messageOf(query.error)} action={<Button size="sm" onClick={() => query.refetch()}>重试</Button>} /> :
        query.isPending ? <p role="status" className="py-8 text-center text-xs text-muted-foreground">正在读取数据明细…</p> :
          query.data.items.length === 0 ? <EmptyState title="没有符合条件的记录" description={dataset?.scope === 'subscribed' && !dataset.total ? '业务首次使用后会自动建立订阅。' : '请调整筛选条件。'} /> :
            <div className="max-h-[45vh] overflow-auto rounded-lg border border-border/60" aria-busy={query.isFetching}>
              <table className="w-full min-w-[680px] text-left text-xs">
                <thead className="sticky top-0 bg-elevated text-muted-foreground"><tr>{['证券 / 来源', '状态', '数据时间', '最近检查', '来源 / 问题'].map(label => <th className="p-2.5 font-medium" key={label}>{label}</th>)}</tr></thead>
                <tbody>{query.data.items.map((row, index) => <tr key={row.symbol + ':' + index} className="border-t border-border/50 align-top">
                  <td className="max-w-48 break-words p-2.5"><p className="font-medium">{row.name}</p><p className="mt-1 font-mono text-muted-foreground">{row.symbol}</p></td>
                  <td className="whitespace-nowrap p-2.5"><Badge variant={row.status === 'fresh' ? 'success' : row.status === 'failed' ? 'danger' : 'warning'}>{freshnessNames[row.status]}</Badge></td>
                  <td className="whitespace-nowrap p-2.5">{row.data_time || '上游未提供'}</td>
                  <td className="whitespace-nowrap p-2.5 text-muted-foreground">{formatDate(row.checked_at)}</td>
                  <td className="max-w-64 break-words p-2.5"><p>{row.source || '—'}</p>{row.error && <p className="mt-1 text-danger">{row.error}</p>}</td>
                </tr>)}</tbody>
              </table>
            </div>}
      {query.isPlaceholderData && <p role="status" className="text-xs text-muted-foreground">正在更新筛选结果…</p>}
      {query.data && <PageNavigation page={filter.page} total={query.data.total} disabled={query.isPlaceholderData} onChange={page => setFilter(value => ({ ...value, page }))} />}
      <p className="text-[11px] text-muted-foreground">数据时间以来源发布期为准；“导出全部”包含当前数据集的所有记录。</p>
    </div>
  </Modal>;
}
