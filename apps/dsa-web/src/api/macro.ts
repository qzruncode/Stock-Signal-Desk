import apiClient from "./index";

export const macroApi = {
  /**
   * Fetch index data for a specific index code.
   */
  getIndexData(indexCode: string, days = 20) {
    return apiClient
      .get(`/api/v1/macro/index`, {
        params: { index_code: indexCode, days },
      })
      .then((r) => r.data);
  },

  /**
   * Fetch government bond yield curve.
   * @param country - "cn" | "us"
   * @param term - "2y" | "5y" | "10y" | "30y"
   */
  getBondYield(country = "cn", term = "10y") {
    return apiClient
      .get(`/api/v1/macro/bond-yield`, {
        params: { country, term },
      })
      .then((r) => r.data);
  },

  /**
   * Fetch macro economic indicator.
   * @param indicator - "PMI" | "CPI" | "PPI" | "GDP" | "M2" | "社融" | "LPR"
   * @param months - Number of recent months (default 12)
   */
  getIndicator(indicator: string, months = 12) {
    return apiClient
      .get(`/api/v1/macro/indicator`, {
        params: { indicator, months },
      })
      .then((r) => r.data);
  },

  /**
   * Fetch sector fund flow ranking.
   * @param type - "industry" | "concept"
   * @param topN - Number of top sectors (default 10)
   */
  getSectorFlow(type: string = "industry", topN = 10) {
    return apiClient
      .get(`/api/v1/macro/sector-flow`, {
        params: { type, top_n: topN },
      })
      .then((r) => r.data);
  },

  /**
   * Fetch market breadth data.
   */
  getMarketBreadth() {
    return apiClient
      .get(`/api/v1/macro/market-breadth`)
      .then((r) => r.data);
  },
};
