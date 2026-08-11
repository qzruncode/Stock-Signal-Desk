import { beforeEach, describe, expect, it, vi } from 'vitest';
import { indicatorScreeningApi } from '../indicatorScreening';

const mockPost = vi.hoisted(() => vi.fn());

vi.mock('../index', () => ({
  default: { post: mockPost },
}));

const plan = {
  version: '1.0' as const,
  combination: 'all' as const,
  conditions: [],
  scope: { type: 'all' as const, groupId: null },
  universe: {
    status: 'active' as const,
    markets: ['sh'] as Array<'sh' | 'sz' | 'bj'>,
    includeSt: false,
    minListingTradingDays: 250,
    priceAdjustment: 'qfq' as const,
  },
  financialFilters: [],
  sort: { field: 'code' as const, order: 'asc' as const },
  outputFields: ['current_atr_pct'],
  previewLimit: 20,
};

describe('indicatorScreeningApi.run', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('keeps result columns aligned with camel-cased metric rows', async () => {
    mockPost.mockResolvedValue({
      data: {
        columns: [
          { field: 'code', label: '股票代码', format: 'text' },
          { field: 'current_atr_pct', label: '当前ATR相对波动率(%)', format: 'percent' },
        ],
        items: [{ code: '000630', current_atr_pct: 3.6191 }],
        matched_codes: ['000630'],
        total: 1,
      },
    });

    const result = await indicatorScreeningApi.run(plan);

    expect(result.columns?.[1].field).toBe('currentAtrPct');
    expect(result.items?.[0].currentAtrPct).toBe(3.6191);
    expect(mockPost).toHaveBeenCalledWith(
      '/api/v1/indicator-screening/run',
      expect.objectContaining({ include_all_items: true }),
      expect.any(Object),
    );
  });
});
