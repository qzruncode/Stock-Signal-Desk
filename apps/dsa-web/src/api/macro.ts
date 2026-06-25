import apiClient from "./index";

export const macroApi = {
  /**
   * Fetch index data for a specific index code.
   */
  getIndexData(indexCode: string, days = 20, signal?: AbortSignal) {
    return apiClient
      .get(`/api/v1/macro/index`, {
        params: { index_code: indexCode, days },
        signal,
      })
      .then((r) => r.data);
  },

  /**
   * Fetch government bond yield curve.
   * @param country - "cn" | "us"
   * @param term - "2y" | "5y" | "10y" | "30y"
   */
  getBondYield(country = "cn", term = "10y", signal?: AbortSignal) {
    return apiClient
      .get(`/api/v1/macro/bond-yield`, {
        params: { country, term },
        signal,
      })
      .then((r) => r.data);
  },

  /**
   * Fetch macro economic indicator.
   * @param indicator - "PMI" | "CPI" | "PPI" | "GDP" | "M2" | "社融" | "LPR"
   * @param months - Number of recent months (default 12)
   */
  getIndicator(indicator: string, months = 12, signal?: AbortSignal) {
    return apiClient
      .get(`/api/v1/macro/indicator`, {
        params: { indicator, months },
        signal,
      })
      .then((r) => r.data);
  },

  /**
   * Fetch sector fund flow ranking.
   * @param type - "industry" | "concept"
   * @param topN - Number of top sectors (default 10)
   */
  getSectorFlow(type: string = "industry", topN = 10, signal?: AbortSignal) {
    return apiClient
      .get(`/api/v1/macro/sector-flow`, {
        params: { type, top_n: topN },
        signal,
      })
      .then((r) => r.data);
  },

  /**
   * Fetch market breadth data.
   */
  getMarketBreadth(signal?: AbortSignal) {
    return apiClient
      .get(`/api/v1/macro/market-breadth`, { signal })
      .then((r) => r.data);
  },
};
