import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import View from '../DataMaintenanceSettingsView';
import { dataMaintenanceApi as api, type CoverageRow, type DatasetOverview, type DatasetSummary, type JobDetail, type SyncJob } from '../../../api/dataMaintenance';
import { useDataServiceStream } from '../../../hooks/useDataServiceStream';
import { datasetNames } from './presentation';

vi.mock('../../../api/dataMaintenance', async importOriginal => ({ ...await importOriginal<typeof import('../../../api/dataMaintenance')>(), dataMaintenanceApi: {
  overview: vi.fn(), jobs: vi.fn(), coverage: vi.fn(), sync: vi.fn(), policy: vi.fn(), job: vi.fn(), cancel: vi.fn(), retry: vi.fn(), exportCoverage: vi.fn(),
} }));
vi.mock('../../../hooks/useDataServiceStream', () => ({ useDataServiceStream: vi.fn() }));

const job: SyncJob = { id: 'running-job', dataset: 'kline', status: 'running', trigger: 'manual', mode: 'stale', total: 30,
  progress: 8, succeeded: 8, failed: 0, message: '正在采集', error: null, created_at: '2026-09-07T07:00:00Z', started_at: null, finished_at: null, cancel_requested: false };
const failedJob: SyncJob = { ...job, id: 'failed-job', dataset: 'financials', status: 'partial', failed: 2, progress: 30, succeeded: 28, message: '部分项目失败' };
const row = (symbol: string, fields: Partial<CoverageRow> = {}): CoverageRow => ({ symbol, name: '测试证券' + symbol, status: 'stale', data_time: '2026-09-04',
  checked_at: '2026-09-07T07:00:00Z', last_success_at: null, source: 'fixture', version: null, error: null, ...fields });
const detail = (value: SyncJob, fields: Partial<JobDetail> = {}): JobDetail => ({ ...value, items_total: 30, page: 1,
  items: [{ symbol: '000001', status: 'failed', attempts: 2, error: '来源暂时不可用' }], ...fields });
function overview(): DatasetOverview {
  return { event_cursor: '1000-0', checked_at: new Date().toISOString(), service: { status: 'ok', database: 'postgresql', provider: 'fixture', checked_at: new Date().toISOString(),
    components: Object.fromEntries(['worker', 'sync-worker', 'scheduler', 'events'].map(name => [name, { healthy: true, last_seen_at: new Date().toISOString(), detail: '' }])) },
  items: Object.entries(datasetNames).map(([id, label]) => ({ id, label, scope: ['securities', 'calendar', 'kline', 'financials'].includes(id) ? 'market' : 'subscribed',
    total: 30, fresh: 25, stale: 5, missing: 0, failed: 0, partial: 0, unknown: 0, coverage_percent: 83.33, latest_data_time: '2026-09-07', oldest_data_time: null,
    policy: { enabled: true, interval_seconds: 3600, max_age_seconds: 7200 }, latest_job: null } as DatasetSummary)) };
}
const clients: QueryClient[] = [];
function show(data = overview()) {
  vi.mocked(api.overview).mockResolvedValue(data);
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(cache);
  const view = render(<QueryClientProvider client={cache}><View /></QueryClientProvider>);
  return { cache, ...view };
}
async function open(name: string, title: string) {
  const button = await screen.findByRole('button', { name });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  return within(await screen.findByRole('dialog', { name: title }));
}
async function select(label: string, option: string) {
  fireEvent.click(screen.getByRole('combobox', { name: label }));
  fireEvent.click(within(await screen.findByRole('listbox')).getByRole('option', { name: option }));
}
function change(label: string, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }); }
beforeEach(() => {
  vi.resetAllMocks();
  // jsdom has no layout/scroll implementation; retain the real Radix select behavior.
  Element.prototype.scrollIntoView = vi.fn();
  vi.mocked(useDataServiceStream).mockReturnValue({ status: 'live', now: Date.now(), reconnect: vi.fn() });
  vi.mocked(api.jobs).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.coverage).mockResolvedValue({ items: [row('000001')], total: 30 });
  vi.mocked(api.job).mockImplementation(async id => detail({ ...(id === failedJob.id ? failedJob : job), id }));
  vi.mocked(api.policy).mockImplementation(async (_, policy) => policy);
});
afterEach(() => { cleanup(); clients.splice(0).forEach(cache => cache.clear()); });

describe('on-demand data maintenance actions', () => {
  it('keeps the initial page compact without preloading detail queries', async () => {
    show();
    expect(await screen.findByRole('button', { name: '自动同步' })).toBeEnabled();
    expect(screen.getAllByRole('article')).toHaveLength(4);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.jobs).toHaveBeenCalledExactlyOnceWith('kline', 1);
    expect(api.coverage).not.toHaveBeenCalled();
    expect(api.job).not.toHaveBeenCalled();
    expect(api.policy).not.toHaveBeenCalled();
  });

  it('saves policy only on explicit submit, converts minutes, and retains the saved values on reopen', async () => {
    const { cache } = show();
    const panel = await open('自动同步', '自动同步设置');
    expect(panel.getByRole('button', { name: '保存设置' })).toBeDisabled();
    change('同步间隔（分钟）', '30');
    change('允许延迟（分钟）', '60');
    fireEvent.click(panel.getByRole('checkbox', { name: '启用自动同步' }));
    expect(api.policy).not.toHaveBeenCalled();
    await waitFor(() => expect(panel.getByRole('button', { name: '保存设置' })).toBeEnabled());
    fireEvent.click(panel.getByRole('button', { name: '保存设置' }));
    expect(await panel.findByText('自动同步设置已保存')).toBeInTheDocument();
    expect(api.policy).toHaveBeenCalledExactlyOnceWith('kline', { enabled: false, interval_seconds: 1800, max_age_seconds: 3600 });
    expect(cache.getQueryData<DatasetOverview>(['data-service', 'overview'])?.items.find(item => item.id === 'kline')?.policy.enabled).toBe(false);
    fireEvent.click(panel.getByRole('button', { name: '关闭' }));
    await open('自动同步', '自动同步设置');
    expect(screen.getByLabelText('同步间隔（分钟）')).toHaveValue(30);
    expect(screen.getByRole('checkbox', { name: '启用自动同步' })).not.toBeChecked();
  });

  it('exposes all ten datasets and validates the interval and allowed delay together', async () => {
    show();
    const panel = await open('自动同步', '自动同步设置');
    fireEvent.click(panel.getByRole('combobox', { name: '选择自动同步数据集' }));
    const options = within(await screen.findByRole('listbox'));
    expect(options.getAllByRole('option').filter(option => option.getAttribute('aria-disabled') !== 'true')).toHaveLength(10);
    fireEvent.click(options.getByRole('option', { name: datasetNames.news }));
    expect(panel.getByText('按近期使用的订阅维护，当前 30 项订阅。')).toBeInTheDocument();
    change('同步间隔（分钟）', '0.1');
    expect(await panel.findByText('最短 0.5 分钟')).toBeInTheDocument();
    change('同步间隔（分钟）', '150');
    expect(await panel.findByText('允许延迟不能小于同步间隔')).toBeInTheDocument();
    expect(panel.getByRole('button', { name: '保存设置' })).toBeDisabled();
    change('允许延迟（分钟）', '180');
    await waitFor(() => expect(panel.getByRole('button', { name: '保存设置' })).toBeEnabled());
    fireEvent.click(panel.getByRole('button', { name: '保存设置' }));
    await waitFor(() => expect(api.policy).toHaveBeenCalledWith('news', { enabled: true, interval_seconds: 9000, max_age_seconds: 10800 }));
  });

  it('prevents dismissing an in-flight save, then displays a recoverable save error', async () => {
    let reject: (error: Error) => void = () => {};
    vi.mocked(api.policy).mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; }));
    show();
    const panel = await open('自动同步', '自动同步设置');
    change('同步间隔（分钟）', '30');
    await waitFor(() => expect(panel.getByRole('button', { name: '保存设置' })).toBeEnabled());
    fireEvent.click(panel.getByRole('button', { name: '保存设置' }));
    await waitFor(() => expect(panel.queryByRole('button', { name: '关闭' })).not.toBeInTheDocument());
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await act(async () => reject(new Error('保存失败，请重试')));
    expect(await panel.findByText('保存失败，请重试')).toBeInTheDocument();
    expect(panel.getByRole('button', { name: '保存设置' })).toBeEnabled();
    expect(panel.getByRole('button', { name: '关闭' })).toBeInTheDocument();
  });

  it('searches, filters and paginates coverage on demand and exports the selected dataset', async () => {
    vi.mocked(api.coverage).mockImplementation(async (_, params) => ({ items: [row(params.page === 2 ? '000021' : '000001')], total: 30 }));
    show();
    const panel = await open('查看数据明细', '数据明细');
    expect(await panel.findByText('000001')).toBeInTheDocument();
    fireEvent.click(panel.getByRole('button', { name: '下一页' }));
    expect(await panel.findByText('000021')).toBeInTheDocument();
    await select('按数据状态筛选', '采集失败');
    await waitFor(() => expect(api.coverage).toHaveBeenLastCalledWith('kline', { page: 1, status: 'failed', search: '' }));
    change('搜索证券或来源', '  000001  ');
    fireEvent.click(panel.getByRole('button', { name: '搜索' }));
    await waitFor(() => expect(api.coverage).toHaveBeenLastCalledWith('kline', { page: 1, status: 'failed', search: '000001' }));
    await select('选择明细数据集', datasetNames.news);
    await waitFor(() => expect(api.coverage).toHaveBeenLastCalledWith('news', { page: 1, status: 'all', search: '' }));
    expect(screen.getByLabelText('搜索证券或来源')).toHaveValue('');
    fireEvent.click(panel.getByRole('button', { name: '导出全部' }));
    await waitFor(() => expect(api.exportCoverage).toHaveBeenCalledExactlyOnceWith('news'));
  });

  it('removes coverage observers on close so hidden panels do not request updates', async () => {
    const { cache } = show();
    const panel = await open('查看数据明细', '数据明细');
    expect(await panel.findByText('000001')).toBeInTheDocument();
    fireEvent.click(panel.getByRole('button', { name: '关闭' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await act(async () => cache.invalidateQueries({ queryKey: ['data-service', 'coverage'] }));
    expect(api.coverage).toHaveBeenCalledTimes(1);
  });

  it('shows export and sync failures inside the open coverage panel', async () => {
    vi.mocked(api.exportCoverage).mockRejectedValue(new Error('导出暂不可用'));
    vi.mocked(api.sync).mockRejectedValue(new Error('任务提交失败'));
    show();
    const panel = await open('查看数据明细', '数据明细');
    fireEvent.click(panel.getByRole('button', { name: '导出全部' }));
    expect(await panel.findByText('导出暂不可用')).toBeInTheDocument();
    fireEvent.click(panel.getByRole('button', { name: '更新未达标数据' }));
    expect(await panel.findByText('任务提交失败')).toBeInTheDocument();
    expect(api.sync).toHaveBeenCalledExactlyOnceWith('kline', 'stale');
  });

  it('opens the newly submitted task from a non-primary dataset coverage panel', async () => {
    vi.mocked(api.sync).mockResolvedValue({ ...job, dataset: 'news', id: 'news-job', status: 'queued' });
    vi.mocked(api.job).mockResolvedValue(detail({ ...job, dataset: 'news', id: 'news-job' }));
    show();
    await open('查看数据明细', '数据明细');
    await select('选择明细数据集', datasetNames.news);
    fireEvent.click(screen.getByRole('button', { name: '更新未达标数据' }));
    expect(await screen.findByRole('dialog', { name: datasetNames.news + ' · 任务详情' })).toBeInTheDocument();
    expect(api.sync).toHaveBeenCalledExactlyOnceWith('news', 'stale');
    await waitFor(() => expect(api.job).toHaveBeenCalledWith('news-job', 1, false));
  });

  it('requires confirmation to cancel, then represents a running cancellation honestly', async () => {
    const data = overview(); data.items.find(item => item.id === 'kline')!.latest_job = job;
    vi.mocked(api.cancel).mockResolvedValue({ ...job, cancel_requested: true });
    show(data);
    fireEvent.click(await screen.findByRole('button', { name: '取消任务' }));
    const confirmation = within(screen.getByRole('dialog', { name: '取消同步任务？' }));
    expect(api.cancel).not.toHaveBeenCalled();
    fireEvent.click(confirmation.getByRole('button', { name: '继续执行' }));
    expect(api.cancel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '取消任务' }));
    fireEvent.click(screen.getByRole('button', { name: '确认取消' }));
    await waitFor(() => expect(api.cancel).toHaveBeenCalledExactlyOnceWith(job.id));
    expect(await screen.findByRole('button', { name: '取消中' })).toBeDisabled();
    expect(screen.queryByText('已取消')).not.toBeInTheDocument();
  });

  it('cancels a historical queued task without replacing the newer overview job', async () => {
    const old = { ...job, id: 'old-job', status: 'queued' as const };
    const latest = { ...job, id: 'latest-job', status: 'success' as const, created_at: '2026-09-07T08:00:00Z' };
    const data = overview(); data.items.find(item => item.id === 'kline')!.latest_job = latest;
    vi.mocked(api.jobs).mockResolvedValue({ items: [old], total: 1 });
    vi.mocked(api.cancel).mockResolvedValue({ ...old, status: 'cancelled', cancel_requested: true });
    const { cache } = show(data);
    const panel = await open('同步记录', '同步记录');
    fireEvent.click(await panel.findByRole('button', { name: '取消任务' }));
    fireEvent.click(screen.getByRole('button', { name: '确认取消' }));
    expect(await panel.findByText('已取消')).toBeInTheDocument();
    expect(panel.getByText('任务已取消，已保存的数据会保留。')).toBeInTheDocument();
    expect(cache.getQueryData<DatasetOverview>(['data-service', 'overview'])?.items.find(item => item.id === 'kline')?.latest_job?.id).toBe(latest.id);
  });

  it('retries only unfinished work through the retry API and follows the new durable job ID', async () => {
    const data = overview(); data.items.find(item => item.id === 'financials')!.latest_job = failedJob;
    const next = { ...failedJob, id: 'retry-job', status: 'queued' as const, trigger: 'retry' };
    vi.mocked(api.retry).mockResolvedValue(next);
    vi.mocked(api.job).mockResolvedValue(detail(next));
    show(data);
    fireEvent.click(await screen.findByRole('button', { name: '重试未完成项' }));
    expect(await screen.findByRole('dialog', { name: datasetNames.financials + ' · 任务详情' })).toBeInTheDocument();
    expect(api.retry).toHaveBeenCalledExactlyOnceWith(failedJob.id);
    expect(api.sync).not.toHaveBeenCalled();
    await waitFor(() => expect(api.job).toHaveBeenCalledWith(next.id, 1, false));
  });

  it('disables history retries when that dataset already has an active task', async () => {
    const data = overview(); data.items.find(item => item.id === 'financials')!.latest_job = { ...failedJob, id: 'new-active-job', status: 'running' };
    vi.mocked(api.jobs).mockResolvedValue({ items: [failedJob], total: 1 });
    show(data);
    const panel = await open('同步记录', '同步记录');
    expect(await panel.findByRole('button', { name: '重试未完成项' })).toBeDisabled();
    expect(api.retry).not.toHaveBeenCalled();
  });

  it('preserves history filters and pages across task details, with server-side failed-item paging', async () => {
    vi.mocked(api.jobs).mockResolvedValue({ items: [failedJob], total: 30 });
    show();
    const panel = await open('同步记录', '同步记录');
    await select('筛选同步数据集', datasetNames.financials);
    await waitFor(() => expect(api.jobs).toHaveBeenLastCalledWith('financials', 1));
    fireEvent.click(panel.getByRole('button', { name: '下一页' }));
    await waitFor(() => expect(api.jobs).toHaveBeenLastCalledWith('financials', 2));
    fireEvent.click(panel.getByRole('button', { name: '详情' }));
    expect(await screen.findByText('来源暂时不可用')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: '只看失败项' }));
    await waitFor(() => expect(api.job).toHaveBeenLastCalledWith(failedJob.id, 1, true));
    await waitFor(() => expect(screen.getByRole('button', { name: '下一页' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() => expect(api.job).toHaveBeenLastCalledWith(failedJob.id, 2, true));
    fireEvent.click(screen.getByRole('button', { name: '返回同步记录' }));
    expect(await screen.findByText('共 30 条 · 第 2 页')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '筛选同步数据集' })).toHaveTextContent(datasetNames.financials);
  });

  it('allows offline snapshots to open but disables policy changes, export and task submission', async () => {
    vi.mocked(useDataServiceStream).mockReturnValue({ status: 'reconnecting', now: Date.now(), reconnect: vi.fn() });
    show();
    const policy = await open('自动同步', '自动同步设置');
    expect(policy.getByText('连接中断，恢复后可保存设置。')).toBeInTheDocument();
    expect(policy.getByRole('checkbox', { name: '启用自动同步' })).toBeDisabled();
    fireEvent.click(policy.getByRole('button', { name: '关闭' }));
    const coverage = await open('查看数据明细', '数据明细');
    expect(coverage.getByRole('button', { name: '导出全部' })).toBeDisabled();
    expect(coverage.getByRole('button', { name: '更新未达标数据' })).toBeDisabled();
  });
});
