import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { indicatorScreeningApi } from '../../api/indicatorScreening';
import type { IndicatorCatalogItem, IndicatorScreenResult } from '../../api/indicatorScreening';
import { watchlistApi } from '../../api/watchlist';
import type { WatchlistGroup } from '../../api/watchlist';
import { IndicatorScreeningSettingsView } from './IndicatorScreeningSettingsView';

vi.mock('../../api/indicatorScreening', async () => {
  const actual = await vi.importActual<typeof import('../../api/indicatorScreening')>('../../api/indicatorScreening');
  return {
    ...actual,
    indicatorScreeningApi: {
      listIndicators: vi.fn(),
      run: vi.fn(),
    },
  };
});

vi.mock('../../api/watchlist', async () => {
  const actual = await vi.importActual<typeof import('../../api/watchlist')>('../../api/watchlist');
  return {
    ...actual,
    watchlistApi: {
      listGroups: vi.fn(),
      createGroup: vi.fn(),
      updateGroup: vi.fn(),
    },
  };
});

const defaultSpec = {
  version: '1.0' as const,
  universe: {
    status: 'active' as const,
    markets: ['sh', 'sz', 'bj'] as Array<'sh' | 'sz' | 'bj'>,
    includeSt: false,
    minListingTradingDays: 250,
    priceAdjustment: 'qfq' as const,
  },
  technicalRule: {
    strategy: 'atr_relative_frequency' as const,
    atrPeriod: 14,
    atrAverage: 'sma' as const,
    baselinePeriod: 60,
    baselineAverage: 'sma' as const,
    thresholdOperator: 'divide' as const,
    thresholdValue: 1.27,
    volatilityThresholdPct: 2.8,
    dailyComparison: 'gt' as const,
    lookbackDays: 250,
    minQualifiedDays: 175,
    minQualifiedRatioPct: 70,
  },
  financialFilters: [
    { field: 'revenue_ttm' as const, operator: 'gt' as const, value: 500_000_000 },
    { field: 'deducted_net_profit_ttm' as const, operator: 'gt' as const, value: 0 },
    { field: 'debt_ratio' as const, operator: 'lt' as const, value: 70 },
  ],
  sort: { field: 'qualified_ratio_pct' as const, order: 'desc' as const },
  outputFields: ['current_atr_pct', 'qualified_ratio_pct'],
  previewLimit: 20,
};

const catalog: IndicatorCatalogItem[] = [
  {
    id: 'atr_relative_volatility',
    label: 'ATR 相对波动率',
    description: '测试用 ATR 指标',
    available: true,
    combinationReady: false,
    parameterSchema: [
      {
        key: 'volatility_threshold_pct',
        label: 'ATR 相对波动率阈值 (%)',
        control: 'number',
        min: 0.1,
        max: 100,
        step: 0.1,
        defaultValue: 2.8,
      },
    ],
    specSchema: {},
    defaultSpec,
  },
  {
    id: 'parent_net_profit',
    label: '归母净利润',
    description: '测试用财务指标',
    available: true,
    combinationReady: true,
    parameterSchema: [
      {
        key: 'operator',
        label: '比较',
        control: 'select',
        options: [
          { value: 'gt', label: '>' },
          { value: 'gte', label: '≥' },
          { value: 'lt', label: '<' },
          { value: 'lte', label: '≤' },
          { value: 'eq', label: '=' },
        ],
        defaultValue: 'gt',
      },
      {
        key: 'value',
        label: '阈值（元）',
        control: 'number',
        step: 1,
        defaultValue: 0,
      },
    ],
    specSchema: {},
    defaultSpec,
  },
  {
    id: 'debt_ratio',
    label: '负债率',
    description: '测试用财务指标',
    available: true,
    combinationReady: true,
    parameterSchema: [
      {
        key: 'operator',
        label: '比较',
        control: 'select',
        options: [{ value: 'lt', label: '<' }],
        defaultValue: 'lt',
      },
      {
        key: 'value',
        label: '阈值（%）',
        control: 'number',
        step: 0.1,
        defaultValue: 70,
      },
    ],
    specSchema: {},
    defaultSpec,
  },
  {
    id: 'annual_revenue',
    label: '年营业收入',
    description: '测试用财务指标',
    available: true,
    combinationReady: true,
    parameterSchema: [
      {
        key: 'operator',
        label: '比较',
        control: 'select',
        options: [{ value: 'gt', label: '>' }],
        defaultValue: 'gt',
      },
      {
        key: 'value',
        label: '阈值（元）',
        control: 'number',
        step: 1,
        defaultValue: 500_000_000,
      },
    ],
    specSchema: {},
    defaultSpec,
  },
];

const existingGroup: WatchlistGroup = {
  id: '7',
  name: '已有分组',
  codes: ['600000'],
  source: 'manual',
};

const result: IndicatorScreenResult = {
  indicator: 'atr_relative_volatility',
  success: true,
  partial: false,
  errors: [],
  warnings: [],
  columns: [{ field: 'code', label: '股票代码', format: 'text' }],
  items: [{ code: '600519' }],
  matchedCodes: ['600519'],
  total: 1,
  appliedRules: ['测试规则'],
  dataTime: '2026-08-08',
};

describe('IndicatorScreeningSettingsView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(indicatorScreeningApi.listIndicators).mockResolvedValue(catalog);
    vi.mocked(watchlistApi.listGroups).mockResolvedValue([existingGroup]);
    vi.mocked(indicatorScreeningApi.run).mockResolvedValue(result);
    vi.mocked(watchlistApi.createGroup).mockResolvedValue({
      id: '8',
      name: '波动率候选',
      codes: ['600519'],
      source: 'indicator_screener',
    });
  });

  it('runs the selected indicator and saves all matches into a shared group', async () => {
    render(<IndicatorScreeningSettingsView />);

    expect(await screen.findByRole('heading', { name: '指标选股' })).toBeInTheDocument();
    expect(screen.queryByText('添加指标条件，组合后筛选并保存到股票分组。')).not.toBeInTheDocument();
    expect(screen.queryByText('1 条')).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '组合' })).not.toBeInTheDocument();
    expect(screen.queryByText('配置条件后运行筛选，结果会显示在这里。')).not.toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '指标' })).toBeInTheDocument();
    expect(screen.queryByLabelText('ATR 周期')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('ATR 相对波动率阈值 (%)')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('combobox', { name: '指标' }));
    expect(screen.queryByText('✓')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '设置条件 1：筛选范围', hidden: true })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '设置条件 1：ATR 相对波动率', hidden: true })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '设置条件 1：归母净利润', hidden: true })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '设置条件 1：负债率', hidden: true })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '设置条件 1：年营业收入', hidden: true })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '设置条件 1：ATR 相对波动率', hidden: true }));
    expect(screen.getByRole('dialog', { name: 'ATR 相对波动率设置' })).toBeInTheDocument();
    expect(screen.getByLabelText('ATR 相对波动率阈值 (%)')).toHaveValue(2.8);

    fireEvent.change(screen.getByLabelText('ATR 相对波动率阈值 (%)'), { target: { value: '3.2' } });
    fireEvent.click(screen.getByRole('button', { name: '完成' }));
    expect(screen.queryByRole('dialog', { name: 'ATR 相对波动率设置' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '运行筛选' }));

    await waitFor(() => expect(indicatorScreeningApi.run).toHaveBeenCalledWith(
      expect.objectContaining({
        version: '1.0',
        combination: 'all',
        scope: { type: 'all', groupId: null },
        conditions: [expect.objectContaining({
          indicator: 'atr_relative_volatility',
          parameters: expect.objectContaining({ volatility_threshold_pct: 3.2 }),
        })],
      }),
    ));
    expect(await screen.findByText('命中 1 只股票')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('combobox', { name: '已有分组' }));
    expect(screen.getByRole('option', { name: '已有分组（1）' })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('或新建分组'), { target: { value: '波动率候选' } });
    fireEvent.click(screen.getByRole('button', { name: '保存 1 只股票到分组' }));
    await waitFor(() => expect(watchlistApi.createGroup).toHaveBeenCalledWith(
      '波动率候选',
      ['600519'],
      'indicator_screener',
    ));
  });

  it('adds a financial condition and sends its configured threshold', async () => {
    render(<IndicatorScreeningSettingsView />);

    expect(await screen.findByRole('heading', { name: '指标选股' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '添加条件' }));

    const indicatorSelects = screen.getAllByRole('combobox', { name: '指标' });
    fireEvent.click(indicatorSelects[1]);
    fireEvent.click(screen.getByRole('button', { name: '设置条件 2：归母净利润', hidden: true }));

    expect(screen.getByRole('dialog', { name: '归母净利润设置' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('阈值（元）'), { target: { value: '100000000' } });
    fireEvent.click(screen.getByRole('button', { name: '完成' }));
    fireEvent.click(screen.getByRole('button', { name: '运行筛选' }));

    await waitFor(() => expect(indicatorScreeningApi.run).toHaveBeenCalledWith(
      expect.objectContaining({
        conditions: expect.arrayContaining([
          expect.objectContaining({
            indicator: 'parent_net_profit',
            parameters: { operator: 'gt', value: 100000000 },
          }),
        ]),
      }),
    ));
  });

  it('treats screening scope as a condition and applies the selected group', async () => {
    render(<IndicatorScreeningSettingsView />);

    expect(await screen.findByRole('heading', { name: '指标选股' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '添加条件' }));

    const indicatorSelects = screen.getAllByRole('combobox', { name: '指标' });
    fireEvent.click(indicatorSelects[1]);
    fireEvent.click(screen.getByRole('button', { name: '设置条件 2：筛选范围', hidden: true }));

    expect(screen.getByRole('dialog', { name: '筛选范围设置' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('combobox', { name: '筛选范围分组' }));
    fireEvent.click(screen.getByRole('option', { name: '已有分组（1）' }));
    fireEvent.click(screen.getByRole('button', { name: '完成' }));
    fireEvent.click(screen.getByRole('button', { name: '运行筛选' }));

    await waitFor(() => expect(indicatorScreeningApi.run).toHaveBeenCalledWith(
      expect.objectContaining({
        scope: { type: 'watchlist_group', groupId: '7' },
        conditions: [expect.objectContaining({ indicator: 'atr_relative_volatility' })],
      }),
    ));
    expect(vi.mocked(indicatorScreeningApi.run).mock.calls[0][0].conditions).toHaveLength(1);
  });

  it('disables conditions already used by another row', async () => {
    render(<IndicatorScreeningSettingsView />);

    expect(await screen.findByRole('heading', { name: '指标选股' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '添加条件' }));

    const indicatorSelects = screen.getAllByRole('combobox', { name: '指标' });
    expect(indicatorSelects[1]).toHaveTextContent('归母净利润');
    fireEvent.click(indicatorSelects[1]);

    const atrOption = screen.getByRole('option', { name: /ATR 相对波动率/ });
    expect(atrOption).toHaveAttribute('aria-disabled', 'true');
    expect(screen.queryByRole('button', { name: '设置条件 2：ATR 相对波动率', hidden: true })).not.toBeInTheDocument();
    fireEvent.click(atrOption);

    expect(indicatorSelects[1]).toHaveTextContent('归母净利润');
  });
});
