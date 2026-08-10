import { beforeEach, describe, expect, it, vi } from 'vitest';
import { stocksApi } from '../stocks';

const mockGet = vi.hoisted(() => vi.fn());

vi.mock('../index', () => ({
  default: { get: mockGet },
}));

describe('stocksApi.listAll', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('fetches every page with the active market filters', async () => {
    mockGet
      .mockResolvedValueOnce({
        data: {
          items: [{ code: '000001' }],
          total: 501,
          page: 1,
          page_size: 500,
          total_pages: 2,
        },
      })
      .mockResolvedValueOnce({
        data: {
          items: [{ code: '000002' }],
          total: 501,
          page: 2,
          page_size: 500,
          total_pages: 2,
        },
      });

    const items = await stocksApi.listAll({ search: '平安', market: 'sh' });

    expect(items).toEqual([{ code: '000001' }, { code: '000002' }]);
    expect(mockGet).toHaveBeenNthCalledWith(
      1,
      '/api/v1/stocks',
      expect.objectContaining({
        params: { search: '平安', market: 'sh', page: 1, page_size: 500, count: true },
      }),
    );
    expect(mockGet).toHaveBeenNthCalledWith(
      2,
      '/api/v1/stocks',
      expect.objectContaining({
        params: { search: '平安', market: 'sh', page: 2, page_size: 500, count: true },
      }),
    );
  });
});
