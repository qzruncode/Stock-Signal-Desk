import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { researchApi } from '../api/research';
import { watchlistApi } from '../api/watchlist';
import { WorkspaceQueryProvider } from '../components/layout/WorkspaceQueryProvider';
import ResearchPage from './ResearchPage';

vi.mock('../api/research', () => ({ researchApi: {
  notes: vi.fn(), refresh: vi.fn(), alerts: vi.fn(), history: vi.fn(), saveAlert: vi.fn(), checkAlert: vi.fn(),
} }));
vi.mock('../api/watchlist', () => ({ watchlistApi: { listGroups: vi.fn() } }));
const renderPage = () => render(<MemoryRouter><WorkspaceQueryProvider><ResearchPage /></WorkspaceQueryProvider></MemoryRouter>);

describe('Research workspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(researchApi.notes).mockResolvedValue([]);
    vi.mocked(researchApi.alerts).mockResolvedValue([]);
    vi.mocked(researchApi.history).mockResolvedValue([]);
    vi.mocked(watchlistApi.listGroups).mockResolvedValue([{ id: '42', name: '观察组', codes: ['600519'] }]);
  });

  it('does not enable monitoring or external delivery implicitly', async () => {
    renderPage();
    await screen.findByText('暂无提醒，默认不会发送通知。');
    expect(screen.getByLabelText('启用变化检查')).not.toBeChecked();
    expect(screen.getByLabelText('允许企业微信自动推送')).not.toBeChecked();
    fireEvent.change(screen.getByLabelText('提醒名称'), { target: { value: '财务观察' } });
    fireEvent.change(screen.getByLabelText('目标'), { target: { value: '600519' } });
    fireEvent.click(screen.getByRole('button', { name: '保存提醒' }));
    await waitFor(() => expect(researchApi.saveAlert).toHaveBeenCalledWith(expect.objectContaining({
      name: '财务观察', target: '600519', enabled: false, notification_enabled: false,
    }), undefined));
    expect(researchApi.checkAlert).not.toHaveBeenCalled();
  });

  it('reuses saved groups and supports explicit rule editing', async () => {
    vi.mocked(researchApi.alerts).mockResolvedValue([{
      id: 8, name: '原提醒', target: '42', target_scope: 'watchlist_group', enabled: false, notification_enabled: false,
      parameters: { kinds: ['financial'], interval_seconds: 900, cooldown_seconds: 3600, below_price: null }, state: {}, next_check_at: null,
    }]);
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: '编辑' }));
    await screen.findByRole('option', { name: '观察组（1 只）' });
    fireEvent.change(screen.getByLabelText('提醒名称'), { target: { value: '修改提醒' } });
    fireEvent.click(screen.getByRole('button', { name: '更新提醒' }));
    await waitFor(() => expect(researchApi.saveAlert).toHaveBeenCalledWith(expect.objectContaining({ name: '修改提醒', target: '42' }), 8));
  });

  it('links immutable notes to their original run and shows outcome boundaries', async () => {
    vi.mocked(researchApi.notes).mockResolvedValue([{
      id: 'note', symbol: '600519', verdict: 'watch', run_id: 'old-run', as_of_at: '2026-09-04T12:00:00',
      baseline_trade_date: '2026-09-03', baseline_price: 100, lifecycle_status: 'pending',
      thesis: { blocks: [{ content: '历史证据支持观察', kind: 'recommendation' }] },
      evidence: { items: [{ source_refs: ['https://example.test/source', 'javascript:alert(1)'] }] },
      outcomes: [{ horizon_trading_days: 5, status: 'pending', return_pct: null, max_adverse_excursion_pct: null }],
    }]);
    renderPage();
    expect(await screen.findByRole('link', { name: '查看原始运行' })).toHaveAttribute('href', '/runs?runId=old-run');
    expect(screen.getByText(/不代表成交收益或策略回测/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '来源 1' })).toHaveAttribute('href', 'https://example.test/source');
    expect(screen.queryByRole('link', { name: '来源 2' })).not.toBeInTheDocument();
  });
});
