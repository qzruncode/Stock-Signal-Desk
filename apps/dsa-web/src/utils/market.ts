export interface MarketRule {
  prefix: string[];
  key: string;
  label: string;
  color: string;
}

export const MARKET_RULES: MarketRule[] = [
  { prefix: ['600', '601', '603', '605'], key: 'sh', label: '沪市主板', color: 'bg-red-100 text-red-700' },
  { prefix: ['000', '001', '002', '003'], key: 'sz', label: '深市主板', color: 'bg-blue-100 text-blue-700' },
  { prefix: ['300', '301'], key: 'cyb', label: '创业板', color: 'bg-purple-100 text-purple-700' },
  { prefix: ['688'], key: 'kcb', label: '科创板', color: 'bg-amber-100 text-amber-700' },
  { prefix: ['8', '9'], key: 'bj', label: '北交所', color: 'bg-emerald-100 text-emerald-700' },
];

export function classifyStock(code: string): { market: string; marketLabel: string } {
  const upper = code.toUpperCase();
  if (upper.startsWith('HK')) return { market: 'hk', marketLabel: '港股' };
  if (/^[A-Z]+$/.test(upper) && upper.length <= 5) return { market: 'us', marketLabel: '美股' };
  for (const rule of MARKET_RULES) {
    for (const p of rule.prefix) {
      if (upper.startsWith(p)) return { market: rule.key, marketLabel: rule.label };
    }
  }
  return { market: 'other', marketLabel: '其他' };
}

export const MARKET_GROUP_ORDER = ['sh', 'sz', 'cyb', 'kcb', 'bj', 'hk', 'us', 'other'];

export const MARKET_LABELS: Record<string, string> = {
  sh: '沪市主板', sz: '深市主板', cyb: '创业板', kcb: '科创板',
  bj: '北交所', hk: '港股', us: '美股', other: '其他',
};

export const MARKET_COLORS: Record<string, string> = {
  sh: 'bg-red-100 text-red-700', sz: 'bg-blue-100 text-blue-700',
  cyb: 'bg-purple-100 text-purple-700', kcb: 'bg-amber-100 text-amber-700',
  bj: 'bg-emerald-100 text-emerald-700', hk: 'bg-rose-100 text-rose-700',
  us: 'bg-indigo-100 text-indigo-700', other: 'bg-gray-100 text-gray-600',
};
