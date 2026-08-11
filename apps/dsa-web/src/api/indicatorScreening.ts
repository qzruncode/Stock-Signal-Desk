import apiClient from './index';
import { toCamelCase } from './utils';

export type IndicatorParameterValue = string | number | boolean | null;

export const INDICATOR_SCREEN_SCOPE_ID = 'watchlist_scope';

export interface IndicatorScreenUniverse {
  status: 'active';
  markets: Array<'sh' | 'sz' | 'bj'>;
  includeSt: boolean;
  minListingTradingDays: number;
  priceAdjustment: 'qfq';
  codes?: string[] | null;
}

export interface IndicatorTechnicalRule {
  strategy: 'atr_relative_frequency';
  atrPeriod: number;
  atrAverage: 'sma' | 'ema' | 'wilder';
  baselinePeriod: number;
  baselineAverage: 'sma' | 'ema';
  thresholdOperator: 'multiply' | 'divide';
  thresholdValue: number;
  volatilityThresholdPct: number | null;
  dailyComparison: 'gt' | 'gte' | 'lt' | 'lte' | 'eq';
  lookbackDays: number;
  minQualifiedDays: number | null;
  minQualifiedRatioPct: number | null;
}

export interface IndicatorScreenSpec {
  version: '1.0';
  universe: IndicatorScreenUniverse;
  technicalRule: IndicatorTechnicalRule;
  financialFilters: Array<{
    field: 'revenue_ttm' | 'parent_net_profit_ttm' | 'deducted_net_profit_ttm' | 'debt_ratio';
    operator: 'gt' | 'gte' | 'lt' | 'lte' | 'eq';
    value: number;
  }>;
  sort: {
    field:
      | 'code'
      | 'current_atr_pct'
      | 'long_term_mean_pct'
      | 'dynamic_warning_pct'
      | 'qualified_days'
      | 'qualified_ratio_pct'
      | 'revenue_ttm'
      | 'parent_net_profit_ttm'
      | 'deducted_net_profit_ttm'
      | 'debt_ratio';
    order: 'asc' | 'desc';
  };
  outputFields: string[];
  previewLimit: number;
}

export interface IndicatorParameterOption {
  value: string;
  label: string;
}

export interface IndicatorParameterDefinition {
  key: string;
  label: string;
  control: 'number' | 'select' | 'checkbox';
  min?: number;
  max?: number;
  step?: number;
  options?: IndicatorParameterOption[];
  defaultValue: IndicatorParameterValue;
  hint?: string;
  advanced?: boolean;
}

export interface IndicatorCondition {
  id: string;
  indicator: string;
  parameters: Record<string, IndicatorParameterValue>;
}

export interface IndicatorScreenScope {
  type: 'all' | 'watchlist_group';
  groupId: string | null;
}

export interface IndicatorScreenPlan {
  version: '1.0';
  combination: 'all' | 'any';
  conditions: IndicatorCondition[];
  scope: IndicatorScreenScope;
  universe: IndicatorScreenUniverse;
  financialFilters: IndicatorScreenSpec['financialFilters'];
  sort: IndicatorScreenSpec['sort'];
  outputFields: string[];
  previewLimit: number;
}

export interface IndicatorCatalogItem {
  id: string;
  label: string;
  description: string;
  available: boolean;
  combinationReady: boolean;
  parameterSchema: IndicatorParameterDefinition[];
  specSchema: Record<string, unknown>;
  defaultSpec: IndicatorScreenSpec;
}

export interface IndicatorScreenColumn {
  field: string;
  label: string;
  format: string;
}

export interface IndicatorScreenResult {
  indicator: string;
  success: boolean;
  partial: boolean;
  errors: string[];
  warnings: string[];
  failureStage?: string | null;
  screenSpec?: IndicatorScreenSpec;
  plan?: IndicatorScreenPlan;
  specFingerprint?: string;
  appliedRules?: string[];
  formula?: Record<string, string>;
  columns?: IndicatorScreenColumn[];
  items?: Array<Record<string, unknown>>;
  matchedCodes: string[];
  total: number;
  downloadUrl?: string | null;
  fileId?: string | null;
  dataTime?: string | null;
  dataTimes?: Record<string, string | null>;
  isStale?: boolean | null;
  freshnessUnknown?: boolean;
  financialReportPeriod?: string | null;
  coverage?: Record<string, unknown>;
  source?: string;
}

function toSnakePlan(plan: IndicatorScreenPlan): Record<string, unknown> {
  return {
    version: plan.version,
    combination: plan.combination,
    conditions: plan.conditions.map((condition) => ({
      id: condition.id,
      indicator: condition.indicator,
      // Parameter keys are catalog-owned protocol keys and intentionally stay
      // unchanged so a future indicator can define its own namespace.
      parameters: condition.parameters,
    })),
    scope: {
      type: plan.scope.type,
      group_id: plan.scope.groupId,
    },
    universe: {
      status: plan.universe.status,
      markets: plan.universe.markets,
      include_st: plan.universe.includeSt,
      min_listing_trading_days: plan.universe.minListingTradingDays,
      price_adjustment: plan.universe.priceAdjustment,
    },
    financial_filters: plan.financialFilters,
    sort: plan.sort,
    output_fields: plan.outputFields,
    preview_limit: plan.previewLimit,
  };
}

function toCamelField(field: string): string {
  return field.replace(/_([a-z])/g, (_match, letter: string) => letter.toUpperCase());
}

export const indicatorScreeningApi = {
  async listIndicators(signal?: AbortSignal): Promise<IndicatorCatalogItem[]> {
    const response = await apiClient.get<Record<string, unknown>>(
      '/api/v1/indicator-screening/indicators',
      { signal },
    );
    const payload = toCamelCase<{ indicators: IndicatorCatalogItem[] }>(response.data);
    return payload.indicators || [];
  },

  async run(
    plan: IndicatorScreenPlan,
    signal?: AbortSignal,
  ): Promise<IndicatorScreenResult> {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/indicator-screening/run',
      // The settings page is a data browser, so it needs every matched row.
      // Agent/tool calls keep their normal preview-only response contract.
      { plan: toSnakePlan(plan), include_all_items: true },
      { timeout: 900000, signal },
    );
    const payload = toCamelCase<IndicatorScreenResult>(response.data);
    return {
      ...payload,
      // ``items`` is deep-converted to camelCase by toCamelCase, while the
      // backend keeps column.field as a protocol value inside the array.
      // Normalize both sides so metric cells do not silently render as "—".
      columns: payload.columns?.map((column) => ({
        ...column,
        field: toCamelField(column.field),
      })),
    };
  },
};
