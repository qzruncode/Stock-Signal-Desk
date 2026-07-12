import apiClient from './index';

export interface StockInfo {
  symbol: string;
  // From cninfo (primary source)
  name: string;
  short_name: string;
  industry: string;
  market: string;
  listing_date: string;
  establish_date: string;
  register_capital: number | null;
  main_business: string;
  business_scope: string;
  profile: string;
  website: string;
  // From East Money (supplementary, may be null)
  total_shares: number | null;
  circ_shares: number | null;
  pe_dynamic: number | null;
  pe_static: number | null;
  pb_ratio: number | null;
  total_mv: number | null;
  circ_mv: number | null;
  // Metadata
  _fetched_at?: string;
  _cached?: boolean;
  _cninfo_ok?: boolean;
  _em_ok?: boolean;
}

// ---------------------------------------------------------------------------
// Backend raw shape — /api/v1/stocks/info returns nested Chinese-key objects
// from multiple sources. This adapter flattens them into StockInfo so the rest
// of the app can keep reading English fields.
// ---------------------------------------------------------------------------

/** Raw nested response from the backend (cninfo / eastmoney / ths_business). */
interface RawStockInfo {
  symbol?: string;
  _sources?: string[];
  _fetched_at?: string;
  _cached?: boolean;
  cninfo?: Record<string, unknown>;
  eastmoney?: Record<string, unknown>;
  ths_business?: Record<string, unknown>;
}

function pickStr(src: Record<string, unknown> | undefined, keys: string[]): string {
  if (!src) return '';
  for (const k of keys) {
    const v = src[k];
    if (v != null && String(v).trim() !== '' && String(v).trim() !== '--') {
      return String(v).trim();
    }
  }
  return '';
}

function pickNum(src: Record<string, unknown> | undefined, keys: string[]): number | null {
  const raw = pickStr(src, keys);
  if (!raw) return null;
  // tolerate "12.53亿" / "1253.16万" suffixes from eastmoney
  const m = raw.match(/^(-?\d+(?:\.\d+)?)\s*(亿|万)?$/);
  if (!m) return null;
  const val = parseFloat(m[1]);
  const mult = m[2] === '亿' ? 1e8 : m[2] === '万' ? 1e4 : 1;
  return val * mult;
}

function adaptStockInfo(raw: RawStockInfo): StockInfo {
  const cn = raw.cninfo;
  const em = raw.eastmoney;
  const hasCn = !!cn && Object.keys(cn).length > 0;
  const hasEm = !!em && Object.keys(em).length > 0;

  return {
    symbol: raw.symbol ?? '',
    name: pickStr(cn, ['公司名称', 'name']),
    short_name: pickStr(cn, ['A股简称', 'short_name']),
    industry: pickStr(cn, ['所属行业', '行业']) || pickStr(em, ['行业', '所属行业']),
    market: pickStr(cn, ['所属市场', '市场']) || pickStr(em, ['市场', '所属市场']),
    listing_date: pickStr(cn, ['上市日期', 'listing_date']),
    establish_date: pickStr(cn, ['成立日期', 'establish_date']),
    register_capital: pickNum(cn, ['注册资金', 'register_capital']),
    main_business: pickStr(cn, ['主营业务', 'main_business']),
    business_scope: pickStr(cn, ['经营范围', 'business_scope']),
    profile: pickStr(cn, ['机构简介', '公司简介', 'profile']),
    website: pickStr(cn, ['官方网站', 'website']),
    total_shares: pickNum(em, ['总股本', 'total_shares']),
    circ_shares: pickNum(em, ['流通股', '流通股本', 'circ_shares']),
    // PE/PB are not provided by stock_individual_info_em; they live on the
    // realtime quote (quote.pe_ratio / quote.pb_ratio) instead.
    pe_dynamic: null,
    pe_static: null,
    pb_ratio: null,
    total_mv: pickNum(em, ['总市值', 'total_mv']),
    circ_mv: pickNum(em, ['流通市值', 'circ_mv']),
    _fetched_at: raw._fetched_at,
    _cached: raw._cached,
    _cninfo_ok: hasCn,
    _em_ok: hasEm,
  };
}

export const stockInfoApi = {
  async getInfo(symbol: string, force: boolean = false): Promise<StockInfo> {
    const response = await apiClient.get<RawStockInfo>(
      '/api/v1/stocks/info',
      { params: { symbol, force }, timeout: 30000 },
    );
    return adaptStockInfo(response.data);
  },
};
