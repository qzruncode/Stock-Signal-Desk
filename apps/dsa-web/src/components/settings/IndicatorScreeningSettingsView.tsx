import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity,
  Download,
  Loader2,
  Save,
  Table2,
  UsersRound,
} from 'lucide-react';
import { Badge, Button, Card, CompactSelect, EmptyState, InlineAlert, Input } from '../common';
import {
  INDICATOR_SCREEN_SCOPE_ID,
  indicatorScreeningApi,
  type IndicatorCatalogItem,
  type IndicatorCondition,
  type IndicatorParameterValue,
  type IndicatorScreenScope,
  type IndicatorScreenPlan,
  type IndicatorScreenResult,
} from '../../api/indicatorScreening';
import { watchlistApi, type WatchlistGroup } from '../../api/watchlist';
import { cn } from '../../utils/cn';
import { IndicatorConditionBuilder } from './IndicatorConditionBuilder';

type Notice = { type: 'success' | 'error'; message: string } | null;

const EMPTY_GROUP_VALUE = '';
const COMPACT_BUTTON_CLASS = 'h-7 gap-1 rounded-md px-2 text-[11px]';

function readableError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function conditionFromCatalog(item: IndicatorCatalogItem, id: string): IndicatorCondition {
  return {
    id,
    indicator: item.id,
    parameters: Object.fromEntries(
      item.parameterSchema.map((definition) => [definition.key, definition.defaultValue]),
    ),
  };
}

function scopeCondition(id: string, groupId: string | null = null): IndicatorCondition {
  return {
    id,
    indicator: INDICATOR_SCREEN_SCOPE_ID,
    parameters: { groupId },
  };
}

function planFromCatalog(item: IndicatorCatalogItem): IndicatorScreenPlan {
  const spec = item.defaultSpec;
  return {
    version: '1.0',
    combination: 'all',
    conditions: [conditionFromCatalog(item, 'condition-1')],
    scope: { type: 'all', groupId: null },
    universe: spec.universe,
    financialFilters: spec.financialFilters,
    sort: spec.sort,
    outputFields: spec.outputFields,
    previewLimit: spec.previewLimit,
  };
}

function formatMetric(value: unknown, format?: string): string {
  if (value === null || value === undefined || value === '') return '—';
  if (format === 'percent' && typeof value === 'number') return `${value.toFixed(2)}%`;
  if (format === 'currency_yuan' && typeof value === 'number') {
    return new Intl.NumberFormat('zh-CN', {
      notation: 'compact',
      maximumFractionDigits: 2,
    }).format(value);
  }
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2);
  return String(value);
}

function groupLabel(group: WatchlistGroup): string {
  const name = group.name?.trim();
  return `${name || '未命名分组'}（${group.codes.length}）`;
}

function scopeFromConditions(conditions: IndicatorCondition[]): IndicatorScreenScope {
  const scopeCondition = conditions.find((condition) => condition.indicator === INDICATOR_SCREEN_SCOPE_ID);
  const groupId = scopeCondition?.parameters.groupId;
  return typeof groupId === 'string' && groupId
    ? { type: 'watchlist_group', groupId }
    : { type: 'all', groupId: null };
}

export function IndicatorScreeningSettingsView() {
  const [catalog, setCatalog] = useState<IndicatorCatalogItem[]>([]);
  const [plan, setPlan] = useState<IndicatorScreenPlan | null>(null);
  const [groups, setGroups] = useState<WatchlistGroup[]>([]);
  const [result, setResult] = useState<IndicatorScreenResult | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [busyAction, setBusyAction] = useState<'loading' | 'screening' | 'saving' | null>('loading');
  const [selectedGroupId, setSelectedGroupId] = useState(EMPTY_GROUP_VALUE);
  const [newGroupName, setNewGroupName] = useState('');
  const conditionSequence = useRef(2);

  const loadGroups = useCallback(async () => {
    const nextGroups = await watchlistApi.listGroups();
    setGroups(nextGroups);
    return nextGroups;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setBusyAction('loading');
    Promise.all([
      indicatorScreeningApi.listIndicators(controller.signal),
      watchlistApi.listGroups(),
    ])
      .then(([indicators, nextGroups]) => {
        if (controller.signal.aborted) return;
        setCatalog(indicators);
        setGroups(nextGroups);
        const first = indicators.find((item) => item.available) || indicators[0];
        if (first) setPlan(planFromCatalog(first));
        setNotice(null);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setNotice({ type: 'error', message: readableError(error, '指标选股配置加载失败') });
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusyAction(null);
      });
    return () => controller.abort();
  }, []);

  const handleAddCondition = useCallback(() => {
    const id = `condition-${conditionSequence.current++}`;
    setPlan((current) => {
      if (!current) return current;
      const usedIndicators = new Set(current.conditions.map((condition) => condition.indicator));
      const first = catalog.find((item) => item.available && !usedIndicators.has(item.id));
      const nextCondition = first
        ? conditionFromCatalog(first, id)
        : !usedIndicators.has(INDICATOR_SCREEN_SCOPE_ID)
          ? scopeCondition(id)
          : null;
      return nextCondition
        ? { ...current, conditions: [...current.conditions, nextCondition] }
        : current;
    });
  }, [catalog]);

  const handleRemoveCondition = useCallback((conditionId: string) => {
    setPlan((current) => {
      if (!current || current.conditions.length <= 1) return current;
      const conditions = current.conditions.filter((condition) => condition.id !== conditionId);
      return {
        ...current,
        conditions,
        scope: scopeFromConditions(conditions),
      };
    });
  }, []);

  const handleIndicatorChange = useCallback((conditionId: string, indicatorId: string) => {
    const next = catalog.find((item) => item.id === indicatorId);
    if (!next && indicatorId !== INDICATOR_SCREEN_SCOPE_ID) return;
    setPlan((current) => {
      if (!current) return current;
      if (current.conditions.some((condition) => (
        condition.id !== conditionId && condition.indicator === indicatorId
      ))) return current;
      const conditions = current.conditions.map((condition) => {
        if (condition.id !== conditionId) return condition;
        if (indicatorId === INDICATOR_SCREEN_SCOPE_ID) {
          return {
            id: conditionId,
            indicator: INDICATOR_SCREEN_SCOPE_ID,
            parameters: {
              groupId: current.scope.type === 'watchlist_group' ? current.scope.groupId : null,
            },
          };
        }
        return conditionFromCatalog(next!, conditionId);
      });
      return { ...current, conditions, scope: scopeFromConditions(conditions) };
    });
    setResult(null);
    setNotice(null);
  }, [catalog]);

  const handleParameterChange = useCallback((
    conditionId: string,
    key: string,
    value: IndicatorParameterValue,
  ) => {
    setPlan((current) => {
      if (!current) return current;
      const conditions = current.conditions.map((condition) => (
        condition.id === conditionId
          ? { ...condition, parameters: { ...condition.parameters, [key]: value } }
          : condition
      ));
      return { ...current, conditions, scope: scopeFromConditions(conditions) };
    });
    setResult(null);
    setNotice(null);
  }, []);

  const handleScreen = useCallback(async () => {
    if (!plan) return;
    const conditions = plan.conditions.filter((condition) => condition.indicator !== INDICATOR_SCREEN_SCOPE_ID);
    if (!conditions.length) {
      setNotice({ type: 'error', message: '请至少保留一个指标条件。' });
      return;
    }
    const executablePlan: IndicatorScreenPlan = {
      ...plan,
      conditions,
      scope: scopeFromConditions(plan.conditions),
    };
    setBusyAction('screening');
    setNotice(null);
    setResult(null);
    try {
      const nextResult = await indicatorScreeningApi.run(executablePlan);
      setResult(nextResult);
      if (nextResult.success) {
        setNotice({
          type: 'success',
          message: `筛选完成：共找到 ${nextResult.total} 只股票。`,
        });
      } else {
        setNotice({
          type: 'error',
          message: nextResult.errors?.[0] || '指标选股未完成。',
        });
      }
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '指标选股执行失败') });
    } finally {
      setBusyAction(null);
    }
  }, [plan]);

  const saveResultToGroup = useCallback(async () => {
    const codes = result?.matchedCodes || [];
    if (!codes.length) {
      setNotice({ type: 'error', message: '当前没有可保存的筛选结果。' });
      return;
    }
    const name = newGroupName.trim();
    if (!name && !selectedGroupId) {
      setNotice({ type: 'error', message: '请选择已有分组，或填写新分组名称。' });
      return;
    }
    setBusyAction('saving');
    setNotice(null);
    try {
      if (name) {
        const duplicate = groups.find((group) => group.name.trim() === name);
        if (duplicate) {
          setNotice({ type: 'error', message: `分组「${name}」已经存在，请直接选择它。` });
          return;
        }
        const created = await watchlistApi.createGroup(name, codes, 'indicator_screener');
        setSelectedGroupId(created.id);
        setNewGroupName('');
        await loadGroups();
        setNotice({ type: 'success', message: `已创建分组「${name}」，写入 ${codes.length} 只股票。` });
      } else {
        const target = groups.find((group) => group.id === selectedGroupId);
        if (!target) {
          setNotice({ type: 'error', message: '所选分组已不存在，请重新加载分组。' });
          return;
        }
        await watchlistApi.updateGroup(target.id, { codes });
        await loadGroups();
        setNotice({ type: 'success', message: `已用筛选结果更新「${target.name}」，共 ${codes.length} 只股票。` });
      }
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '保存筛选分组失败') });
    } finally {
      setBusyAction(null);
    }
  }, [groups, loadGroups, newGroupName, result, selectedGroupId]);

  const resultColumns = result?.columns || [];
  const resultItems = result?.items || [];

  if (busyAction === 'loading' && !plan) {
    return (
      <div className="flex min-h-[30vh] items-center justify-center">
        <div className="flex flex-col items-center gap-2 text-secondary-text">
          <Loader2 className="size-6 animate-spin text-cyan" />
          <p className="text-xs">加载指标目录...</p>
        </div>
      </div>
    );
  }

  return (
    <section className="indicator-screening-settings h-full min-h-0 space-y-3 overflow-y-auto pr-1">
      <header className="px-0.5">
        <div className="flex items-start gap-2.5">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-cyan/10 text-cyan">
            <Activity className="size-4" />
          </span>
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-foreground">指标选股</h2>
          </div>
        </div>
      </header>

      {notice ? <InlineAlert variant={notice.type === 'error' ? 'danger' : 'success'} message={notice.message} /> : null}

      {!catalog.length || !plan ? (
        <EmptyState
          title="暂无可用指标"
          description="指标目录暂时无法加载，请刷新设置页重试。"
          icon={<Activity className="size-6" />}
          className="indicator-screening-empty"
        />
      ) : (
        <>
          <Card padding="sm" className="indicator-screening-card indicator-screening-condition-card">
            <IndicatorConditionBuilder
              catalog={catalog}
              conditions={plan.conditions}
              groups={groups}
              onAdd={handleAddCondition}
              onRemove={handleRemoveCondition}
              onIndicatorChange={handleIndicatorChange}
              onParameterChange={handleParameterChange}
            />

            <div className="mt-3 flex justify-end border-t border-border/50 pt-3">
              <Button
                size="sm"
                onClick={() => void handleScreen()}
                disabled={busyAction === 'screening'}
                isLoading={busyAction === 'screening'}
                loadingText="筛选中..."
                className={COMPACT_BUTTON_CLASS}
              >
                <Activity className="size-3.5" />
                运行筛选
              </Button>
            </div>
          </Card>

          {result ? (
            <Card title="筛选结果" subtitle="RESULT" padding="none" className="indicator-screening-card">
              <div className="flex flex-wrap items-center gap-2 border-b border-border/50 px-4 py-3">
                <Badge variant={result.success ? 'success' : 'danger'}>
                  {result.success ? '执行完成' : '执行失败'}
                </Badge>
                <span className="text-sm text-foreground">命中 {result.total} 只股票</span>
                {result.dataTime ? <span className="text-xs text-secondary-text">行情日期：{result.dataTime}</span> : null}
                {result.downloadUrl ? (
                  <a
                    href={result.downloadUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="ml-auto inline-flex items-center gap-1 text-xs text-cyan hover:underline"
                  >
                    <Download className="size-3.5" />
                    下载完整结果
                  </a>
                ) : null}
              </div>

              {result.success && resultItems.length ? (
                <div className="overflow-x-auto">
                  <table className="min-w-full text-left text-xs">
                    <thead className="bg-elevated/50 text-[10px] uppercase tracking-wide text-secondary-text">
                      <tr>
                        {resultColumns.map((column) => <th key={column.field} className="whitespace-nowrap px-3 py-2 font-medium">{column.label}</th>)}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/40">
                      {resultItems.map((item, rowIndex) => (
                        <tr key={String(item.code || rowIndex)} className="hover:bg-elevated/30">
                          {resultColumns.map((column) => (
                            <td key={column.field} className={cn('whitespace-nowrap px-3 py-2 text-foreground', column.field === 'code' && 'font-mono font-medium')}>
                              {formatMetric(item[column.field], column.format)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : result.success ? (
                <EmptyState title="没有符合条件的股票" description="调整条件后重新运行筛选。" className="indicator-screening-empty m-3" />
              ) : null}

              {result.success ? (
                <div className="grid gap-3 border-t border-border/50 p-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.7fr)]">
                  <div>
                    <details className="group">
                      <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium text-foreground">
                        <Table2 className="size-3.5 text-cyan" />
                        查看本次规则（{result.appliedRules?.length || 0} 条）
                        <span className="ml-1 text-[10px] text-secondary-text transition-transform group-open:rotate-180">⌄</span>
                      </summary>
                      <ul className="mt-2 space-y-1 text-[11px] leading-4 text-secondary-text">
                        {(result.appliedRules || []).map((item) => <li key={item}>· {item}</li>)}
                      </ul>
                    </details>
                  </div>
                  <div className="rounded-lg border border-cyan/15 bg-cyan/5 p-3">
                    <div className="mb-2.5 flex items-center gap-2 text-sm font-medium text-foreground">
                      <UsersRound className="size-3.5 text-cyan" />
                      保存到股票分组
                    </div>
                    <CompactSelect
                      ariaLabel="已有分组"
                      value={selectedGroupId}
                      onChange={setSelectedGroupId}
                      className="indicator-screening-field"
                      options={[
                        { value: EMPTY_GROUP_VALUE, label: '不选择已有分组' },
                        ...groups.map((group) => ({ value: group.id, label: groupLabel(group) })),
                      ]}
                    />
                    <div className="mt-2.5">
                      <Input
                        label="或新建分组"
                        value={newGroupName}
                        onChange={(event) => setNewGroupName(event.target.value)}
                        placeholder="例如：高波动候选"
                        hint="填写名称会创建分组；否则更新已选分组。"
                        className="indicator-screening-field"
                      />
                    </div>
              <Button
                size="sm"
                variant="secondary"
                className={cn('mt-2.5 w-full', COMPACT_BUTTON_CLASS)}
                      onClick={() => void saveResultToGroup()}
                      disabled={busyAction === 'saving'}
                      isLoading={busyAction === 'saving'}
                      loadingText="保存中..."
                    >
                      <Save className="size-3.5" />
                      保存 {result.matchedCodes.length} 只股票到分组
                    </Button>
                  </div>
                </div>
              ) : null}
            </Card>
          ) : null}
        </>
      )}
    </section>
  );
}

export default IndicatorScreeningSettingsView;
