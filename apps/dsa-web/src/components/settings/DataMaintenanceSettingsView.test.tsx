import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import View, { DataMaintenanceSettingsView } from './DataMaintenanceSettingsView';
import { dataMaintenanceApi as api, type DatasetId, type DatasetOverview, type DatasetSummary, type SyncJob } from '../../api/dataMaintenance';

vi.mock('../../api/dataMaintenance', async importOriginal => ({ ...await importOriginal<typeof import('../../api/dataMaintenance')>(), dataMaintenanceApi: {
  overview: vi.fn(), jobs: vi.fn(), coverage: vi.fn(), sync: vi.fn(), eventsUrl: vi.fn(),
} }));

const job: SyncJob = { id: 'test-job', dataset: 'kline', status: 'running', trigger: 'manual', mode: 'stale',
  total: 30, progress: 8, succeeded: 8, failed: 0, message: '测试采集任务', error: null,
  created_at: '2026-09-07T07:00:00Z', started_at: null, finished_at: null, cancel_requested: false };
function dataset(id: DatasetId, fields: Partial<DatasetSummary> = {}): DatasetSummary {
  return { id, label: id, scope: 'market', total: 30, fresh: 30, stale: 0, missing: 0, failed: 0, partial: 0, unknown: 0,
    coverage_percent: 100, latest_data_time: '2026-09-07', oldest_data_time: null,
    policy: { enabled: true, interval_seconds: 3600, max_age_seconds: 7200 }, latest_job: null, ...fields };
}
const overview: DatasetOverview = {
  event_cursor: '1000-0', checked_at: '2026-09-07T07:00:00Z',
  service: { status: 'ok', database: 'postgresql', provider: 'live', checked_at: '2026-09-07T07:00:00Z',
    components: Object.fromEntries(['worker', 'sync-worker', 'scheduler', 'events'].map(name => [name, { healthy: true, last_seen_at: new Date().toISOString(), detail: '' }])) },
  items: [dataset('securities', { total: 1, fresh: 1 }), dataset('kline', { fresh: 9, missing: 21, coverage_percent: 30 }),
    dataset('financials', { fresh: 28, stale: 1, partial: 1, latest_data_time: '2026-06-30' }),
    dataset('news', { label: '公司新闻', scope: 'subscribed', total: 0, fresh: 0 })],
};
const clients: QueryClient[] = [];
class Stream extends EventTarget {
  static instances: Stream[] = [];
  url: string;
  readyState = 1;
  close = vi.fn(() => { this.readyState = 2; });
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) { super(); this.url = url; Stream.instances.push(this); queueMicrotask(() => { this.onopen?.(); this.emit('ready', {}); }); }
  emit(type: string, data: unknown, id = '1001-0') { this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(data), lastEventId: id })); }
}
function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(client);
  return render(<QueryClientProvider client={client}><View /></QueryClientProvider>);
}
function push(items: DatasetSummary[], id = '1001-0', jobs: SyncJob[] = []) {
  Stream.instances.at(-1)!.emit('change', { datasets: items, jobs, coverage: {}, service: overview.service, checked_at: overview.checked_at }, id);
}
async function ready() {
  await waitFor(() => expect(screen.getByRole('button', { name: '同步主数据' })).toBeEnabled());
}
beforeEach(() => {
  vi.resetAllMocks();
  Stream.instances = [];
  vi.stubGlobal('EventSource', Stream);
  vi.mocked(api.eventsUrl).mockReturnValue('/api/v1/data-service/events?after=1000-0');
  vi.mocked(api.overview).mockResolvedValue(structuredClone(overview));
  vi.mocked(api.jobs).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.coverage).mockResolvedValue({ items: [], total: 0 });
});
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('restored compact data maintenance', () => {
  it('restores the four statistics and four task rows without the new dashboard clutter', async () => {
    expect(View).toBe(DataMaintenanceSettingsView);
    show();
    await ready();
    expect(screen.getAllByRole('article')).toHaveLength(4);
    expect(within(screen.getByRole('group', { name: '活跃股票统计' })).getByText('30')).toBeInTheDocument();
    expect(within(screen.getByRole('group', { name: '已有 K 线统计' })).getByText('9')).toBeInTheDocument();
    expect(within(screen.getByRole('group', { name: '缺失 K 线统计' })).getByText('21')).toBeInTheDocument();
    expect(within(screen.getByRole('group', { name: '最新财报覆盖统计' })).getByText('28 / 30')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '数据完整性' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '同步任务' })).toBeInTheDocument();
    for (const text of ['数据自动维护', 'DATA SERVICE', '公司新闻', '历史数据已进行质量隔离', '实时推送 · 按变化更新']) {
      expect(screen.queryByText(text)).not.toBeInTheDocument();
    }
    expect(api.coverage).not.toHaveBeenCalled();
    expect(screen.queryByRole('heading', { name: '同步记录' })).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.jobs).toHaveBeenCalledWith('kline', 1);
  });

  it.each([
    ['同步主数据', 'securities', 'stale'],
    ['同步全市场 K 线', 'kline', 'stale'],
    ['补齐缺失 K 线', 'kline', 'missing'],
    ['同步最新财报', 'financials', 'stale'],
  ] as const)('keeps the one-click %s action on the independent service', async (label, dataset, mode) => {
    vi.mocked(api.sync).mockResolvedValue({ ...job, dataset, mode, status: 'queued' });
    show();
    await ready();
    fireEvent.click(screen.getByRole('button', { name: label }));
    await waitFor(() => expect(api.sync).toHaveBeenCalledWith(dataset, mode));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: label })).toBeDisabled());
  });

  it('loads missing codes only on expansion and submits missing mode independently of pagination', async () => {
    const row = (symbol: string) => ({ symbol, name: symbol, status: 'missing' as const, data_time: null, checked_at: null, last_success_at: null, source: null, version: null, error: null });
    vi.mocked(api.coverage).mockImplementation(async (_, params) => ({ total: 21, items: [row(params.page === 1 ? '000001' : '000021')] }));
    vi.mocked(api.sync).mockResolvedValue({ ...job, mode: 'missing', status: 'queued' });
    show();
    await ready();
    expect(api.coverage).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('查看缺失代码（21 只）'));
    expect(await screen.findByText('000001')).toBeInTheDocument();
    expect(api.coverage).toHaveBeenCalledWith('kline', { page: 1, status: 'missing', search: '' });
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findByText('000021')).toBeInTheDocument();
    expect(api.coverage).toHaveBeenLastCalledWith('kline', { page: 2, status: 'missing', search: '' });
    fireEvent.click(screen.getByRole('button', { name: '补齐缺失 K 线' }));
    await waitFor(() => expect(api.sync).toHaveBeenCalledWith('kline', 'missing'));
  });

  it('does not keep requesting hidden missing-code pages after collapse', async () => {
    show();
    await ready();
    const summary = screen.getByText('查看缺失代码（21 只）');
    fireEvent.click(summary);
    await waitFor(() => expect(api.coverage).toHaveBeenCalledTimes(1));
    fireEvent.click(summary);
    await waitFor(() => expect(screen.queryByText('当前页暂无缺失代码')).not.toBeInTheDocument());
    act(() => Stream.instances[0].emit('change', { datasets: [], jobs: [], coverage: { kline: ['000001'] }, service: overview.service, checked_at: overview.checked_at }));
    expect(api.coverage).toHaveBeenCalledTimes(1);
  });

  it('keeps full and missing job states separate while disabling conflicting kline actions', async () => {
    const data = structuredClone(overview);
    data.items[1].latest_job = { ...job, mode: 'missing' };
    vi.mocked(api.overview).mockResolvedValue(data);
    vi.mocked(api.jobs).mockResolvedValue({ items: [{ ...job, id: 'full-job', status: 'success' }], total: 1 });
    show();
    await ready();
    expect(within(screen.getByRole('article', { name: '缺失 K 线' })).getByText('执行中')).toBeInTheDocument();
    expect(within(screen.getByRole('article', { name: '全市场 K 线' })).getByText('已完成')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '同步全市场 K 线' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '补齐缺失 K 线' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '同步最新财报' })).toBeEnabled();
  });

  it('keeps a reappearing missing-code disclosure closed until explicitly opened', async () => {
    show();
    await ready();
    fireEvent.click(screen.getByText('查看缺失代码（21 只）'));
    await waitFor(() => expect(api.coverage).toHaveBeenCalledTimes(1));
    act(() => push([dataset('kline')]));
    expect(screen.queryByText('查看缺失代码（21 只）')).not.toBeInTheDocument();
    act(() => push([dataset('kline', { missing: 1, fresh: 29 })], '1002-0'));
    expect(screen.getByText('查看缺失代码（1 只）')).toBeInTheDocument();
    expect(screen.queryByText('当前页暂无缺失代码')).not.toBeInTheDocument();
    expect(api.coverage).toHaveBeenCalledTimes(1);
  });

  it('disables missing sync when no records are missing, without claiming all data is fresh', async () => {
    const data = structuredClone(overview);
    data.items[1] = dataset('kline', { fresh: 20, stale: 10 });
    vi.mocked(api.overview).mockResolvedValue(data);
    show();
    await ready();
    expect(screen.getByRole('button', { name: '补齐缺失 K 线' })).toBeDisabled();
    expect(screen.getByText('K 线记录齐全')).toBeInTheDocument();
    expect(screen.queryByText('数据完整')).not.toBeInTheDocument();
  });

  it('never marks unavailable or empty coverage as complete', async () => {
    const data = structuredClone(overview);
    data.items = data.items.filter(item => item.id !== 'kline');
    vi.mocked(api.overview).mockResolvedValue(data);
    show();
    await ready();
    expect(screen.getByText('暂时无法读取数据完整性')).toBeInTheDocument();
    expect(screen.queryByText('K 线记录齐全')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '补齐缺失 K 线' })).toBeDisabled();
  });

  it('only shows a service warning when a required component is unhealthy', async () => {
    const data = structuredClone(overview);
    delete data.service.components['sync-worker'];
    vi.mocked(api.overview).mockResolvedValue(data);
    show();
    expect(await screen.findByText('后台维护暂不可用，请检查数据服务。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '同步主数据' })).toBeDisabled();
  });

  it('shows an actionable submission error without starting a second task', async () => {
    vi.mocked(api.sync).mockRejectedValue(new Error('数据服务暂不可用'));
    show();
    await ready();
    fireEvent.click(screen.getByRole('button', { name: '同步最新财报' }));
    expect(await screen.findByText('数据服务暂不可用')).toBeInTheDocument();
    expect(api.sync).toHaveBeenCalledTimes(1);
  });

  it('keeps an idle page on one stream with no periodic HTTP refreshes', async () => {
    vi.useFakeTimers();
    show();
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(Stream.instances).toHaveLength(1);
    const counts = [vi.mocked(api.overview).mock.calls.length, vi.mocked(api.jobs).mock.calls.length];
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect([vi.mocked(api.overview).mock.calls.length, vi.mocked(api.jobs).mock.calls.length]).toEqual(counts);
    expect(api.coverage).not.toHaveBeenCalled();
  });

  it('updates statistics and task progress directly from service events', async () => {
    const data = structuredClone(overview);
    data.items[1].latest_job = job;
    vi.mocked(api.overview).mockResolvedValue(data);
    vi.mocked(api.jobs).mockResolvedValue({ items: [job], total: 1 });
    show();
    await ready();
    const calls = vi.mocked(api.overview).mock.calls.length;
    act(() => push([dataset('kline', { fresh: 10, missing: 20, latest_job: { ...job, progress: 10 } })], '2000-0', [{ ...job, progress: 10 }]));
    expect(within(screen.getByRole('group', { name: '缺失 K 线统计' })).getByText('20')).toBeInTheDocument();
    expect(within(screen.getByRole('article', { name: '全市场 K 线' })).getAllByText('10 / 30')).toHaveLength(2);
    expect(api.overview).toHaveBeenCalledTimes(calls);
    expect(api.jobs).toHaveBeenCalledTimes(1);
  });

  it('keeps an offline snapshot and resumes actions after native transport recovery', async () => {
    show();
    await ready();
    act(() => Stream.instances[0].onerror?.());
    expect(screen.getByText('实时连接中断，显示上次快照，恢复后自动更新。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '同步主数据' })).toBeDisabled();
    expect(within(screen.getByRole('group', { name: '活跃股票统计' })).getByText('30')).toBeInTheDocument();
    act(() => Stream.instances[0].emit('ready', {}));
    await ready();
    expect(Stream.instances).toHaveLength(1);
  });

  it('resynchronizes once after a replay gap and closes subscriptions on unmount', async () => {
    const view = show();
    await ready();
    const calls = vi.mocked(api.overview).mock.calls.length;
    act(() => Stream.instances[0].emit('reset', { reason: 'trimmed' }));
    await waitFor(() => expect(api.overview).toHaveBeenCalledTimes(calls + 1));
    view.unmount();
    expect(Stream.instances.every(stream => stream.close.mock.calls.length > 0)).toBe(true);
  });

  it('does not let a slow HTTP refresh overwrite newer pushed values', async () => {
    show();
    await ready();
    let finish: (value: DatasetOverview) => void = () => {};
    vi.mocked(api.overview).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    fireEvent.click(screen.getByRole('button', { name: '刷新状态' }));
    act(() => push([dataset('financials', { fresh: 29 })], '2000-0'));
    expect(within(screen.getByRole('group', { name: '最新财报覆盖统计' })).getByText('29 / 30')).toBeInTheDocument();
    await act(async () => { finish(structuredClone(overview)); });
    expect(within(screen.getByRole('group', { name: '最新财报覆盖统计' })).getByText('29 / 30')).toBeInTheDocument();
  });

  it('handles browser offline events and ignores late transport callbacks', async () => {
    show();
    await ready();
    act(() => window.dispatchEvent(new Event('offline')));
    expect(Stream.instances[0].close).toHaveBeenCalledTimes(1);
    act(() => Stream.instances[0].emit('ready', {}));
    expect(screen.getByRole('button', { name: '同步主数据' })).toBeDisabled();
    act(() => window.dispatchEvent(new Event('online')));
    await ready();
    expect(Stream.instances).toHaveLength(2);
  });

  it('retains library-based recovery and its cursor after terminal HTTP failures', async () => {
    vi.useFakeTimers();
    show();
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    act(() => { push([], '2500-0'); Stream.instances[0].readyState = 2; Stream.instances[0].onerror?.(); });
    expect(screen.getByRole('button', { name: '同步主数据' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(10001); });
    expect(Stream.instances).toHaveLength(2);
    expect(Stream.instances[1].url).toContain('lastEventId=2500-0');
    expect(screen.getByRole('button', { name: '同步主数据' })).toBeEnabled();
  });
});
