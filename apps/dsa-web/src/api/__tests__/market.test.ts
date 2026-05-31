import { beforeEach, describe, expect, it, vi } from 'vitest';
import { marketApi } from '../market';

const get = vi.hoisted(() => vi.fn());

vi.mock('../index', () => ({
  default: {
    get,
  },
}));

describe('marketApi', () => {
  beforeEach(() => {
    get.mockReset();
    get.mockResolvedValue({
      data: {
        is_trading_time: false,
        up_count: 0,
        down_count: 0,
        flat_count: 0,
        limit_up_count: 0,
        limit_down_count: 0,
        total_amount: 0,
        north_flow: 0,
      },
    });
  });

  it('maps useCache=false to the backend force parameter', async () => {
    await marketApi.getStatus(false);

    expect(get).toHaveBeenCalledWith(
      '/api/v1/market/status',
      { params: { force: true }, timeout: 30000 },
    );
  });

  it('keeps cache enabled by default', async () => {
    await marketApi.getStatus();

    expect(get).toHaveBeenCalledWith(
      '/api/v1/market/status',
      { params: { force: false }, timeout: 30000 },
    );
  });
});
