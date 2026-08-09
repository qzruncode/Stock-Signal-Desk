import { Plus, Settings2, Trash2 } from 'lucide-react';
import { useState } from 'react';
import {
  Button,
  CompactSelect,
  FormCheckbox,
  FormNumberInput,
  FormSelect,
  Modal,
} from '../common';
import { INDICATOR_SCREEN_SCOPE_ID } from '../../api/indicatorScreening';
import type {
  IndicatorCatalogItem,
  IndicatorCondition,
  IndicatorParameterDefinition,
  IndicatorParameterValue,
} from '../../api/indicatorScreening';
import type { WatchlistGroup } from '../../api/watchlist';

interface IndicatorConditionBuilderProps {
  catalog: IndicatorCatalogItem[];
  conditions: IndicatorCondition[];
  groups: WatchlistGroup[];
  onAdd: () => void;
  onRemove: (conditionId: string) => void;
  onIndicatorChange: (conditionId: string, indicatorId: string) => void;
  onParameterChange?: (conditionId: string, key: string, value: IndicatorParameterValue) => void;
}

function parameterValue(
  condition: IndicatorCondition,
  definition: IndicatorParameterDefinition,
): IndicatorParameterValue {
  return condition.parameters[definition.key] ?? definition.defaultValue;
}

function renderParameter(
  condition: IndicatorCondition,
  definition: IndicatorParameterDefinition,
  onChange: (value: IndicatorParameterValue) => void,
) {
  const value = parameterValue(condition, definition);
  if (definition.control === 'select') {
    return (
      <FormSelect
        label={definition.label}
        layout="inline"
        labelClassName="w-28"
        ariaLabel={definition.label}
        value={String(value ?? '')}
        onChange={onChange}
        options={definition.options || []}
        density="compact"
        controlClassName="indicator-screening-field"
      />
    );
  }
  if (definition.control === 'checkbox') {
    return (
      <FormCheckbox
        label={definition.label}
        layout="inline"
        labelClassName="w-28"
        ariaLabel={definition.label}
        checked={value === true}
        onChange={onChange}
      />
    );
  }
  return (
    <FormNumberInput
      label={definition.label}
      layout="inline"
      labelClassName="w-28"
      ariaLabel={definition.label}
      min={definition.min}
      max={definition.max}
      step={definition.step}
      value={value ?? ''}
      onChange={onChange}
      hint={definition.hint}
      controlClassName="indicator-screening-field"
    />
  );
}

export function IndicatorConditionBuilder({
  catalog,
  conditions,
  groups,
  onAdd,
  onRemove,
  onIndicatorChange,
  onParameterChange,
}: IndicatorConditionBuilderProps) {
  const availableIndicators = catalog.filter((item) => item.available);
  const indicatorOptions = [
    { value: INDICATOR_SCREEN_SCOPE_ID, label: '筛选范围' },
    ...availableIndicators.map((item) => ({ value: item.id, label: item.label })),
  ];
  const scopeOptions = [
    { value: '', label: '全部股票' },
    ...groups.map((group) => ({
      value: group.id,
      label: `${group.name?.trim() || '未命名分组'}（${group.codes.length}）`,
    })),
  ];
  const usedIndicatorIds = new Set(conditions.map((condition) => condition.indicator));
  const hasUnusedIndicator = indicatorOptions.some((option) => !usedIndicatorIds.has(option.value));
  const [settingsConditionId, setSettingsConditionId] = useState<string | null>(null);
  const settingsCondition = conditions.find((condition) => condition.id === settingsConditionId) || null;
  const settingsIndicator = settingsCondition
    ? catalog.find((item) => item.id === settingsCondition.indicator) || null
    : null;
  const settingsIsScope = settingsCondition?.indicator === INDICATOR_SCREEN_SCOPE_ID;
  const settingsScopeValue = settingsCondition?.parameters.groupId == null
    ? ''
    : String(settingsCondition.parameters.groupId);

  return (
    <div className="indicator-condition-builder">
      <div className="flex flex-wrap items-end justify-between gap-2.5">
        <h3 className="text-sm font-semibold text-foreground">筛选条件</h3>
        <Button size="sm" variant="outline" className="h-7 gap-1 rounded-md px-2 text-[11px]" onClick={onAdd} disabled={!availableIndicators.length || conditions.length >= 8 || !hasUnusedIndicator}>
          <Plus className="size-3.5" />
          添加条件
        </Button>
      </div>

      <div className="mt-2 space-y-1.5">
        {conditions.map((condition, index) => {
          const indicator = catalog.find((item) => item.id === condition.indicator);
          const isScopeCondition = condition.indicator === INDICATOR_SCREEN_SCOPE_ID;
          const conditionOptions = indicatorOptions.map((option) => ({
            ...option,
            disabled: option.value !== condition.indicator && usedIndicatorIds.has(option.value),
          }));
          return (
            <div key={condition.id} className="rounded-lg border border-border/60 bg-elevated/25 p-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-flex h-6 items-center rounded-md bg-cyan/10 px-2 text-[11px] font-medium text-cyan">
                  条件 {index + 1}
                </span>
                <CompactSelect
                  ariaLabel="指标"
                  value={condition.indicator}
                  onChange={(value) => onIndicatorChange(condition.id, value)}
                  options={conditionOptions}
                  className="indicator-screening-field min-w-52 flex-1"
                  menuBehavior="overlay"
                  renderOptionAction={(option) => {
                    if (option.value === INDICATOR_SCREEN_SCOPE_ID) {
                      return {
                        ariaLabel: `设置条件 ${index + 1}：筛选范围`,
                        title: '设置筛选范围',
                        onClick: () => {
                          if (condition.indicator !== INDICATOR_SCREEN_SCOPE_ID) {
                            onIndicatorChange(condition.id, INDICATOR_SCREEN_SCOPE_ID);
                          }
                          setSettingsConditionId(condition.id);
                        },
                        children: <Settings2 className="size-3.5" />,
                      };
                    }
                    const optionIndicator = availableIndicators.find((item) => item.id === option.value);
                    if (!optionIndicator || !onParameterChange || !optionIndicator.parameterSchema.length) return undefined;
                    return {
                      ariaLabel: `设置条件 ${index + 1}：${optionIndicator.label}`,
                      title: `设置${optionIndicator.label}参数`,
                      onClick: () => {
                        if (condition.indicator !== optionIndicator.id) {
                          onIndicatorChange(condition.id, optionIndicator.id);
                        }
                        setSettingsConditionId(condition.id);
                      },
                      children: <Settings2 className="size-3.5" />,
                    };
                  }}
                />
                <Button
                  size="sm"
                  variant="ghost"
                  className="size-7 shrink-0 px-0 text-secondary-text hover:text-danger"
                  onClick={() => onRemove(condition.id)}
                  disabled={conditions.length <= 1}
                  aria-label={`删除条件 ${index + 1}`}
                  title={conditions.length <= 1 ? '至少保留一个条件' : '删除条件'}
                >
                  <Trash2 className="size-3" />
                </Button>
              </div>

              {!indicator && !isScopeCondition ? (
                <p className="mt-2 text-xs text-danger">该指标已从目录移除，请重新选择。</p>
              ) : null}
            </div>
          );
        })}
      </div>

      {settingsCondition && onParameterChange && (settingsIndicator || settingsIsScope) ? (
        <Modal
          isOpen
          onClose={() => setSettingsConditionId(null)}
          title={`${settingsIsScope ? '筛选范围' : settingsIndicator?.label}设置`}
          width="max-w-sm"
          className="indicator-screening-parameter-modal-shell"
          footer={(
            <div className="flex justify-end">
              <Button
                size="sm"
                onClick={() => setSettingsConditionId(null)}
                className="h-7 gap-1 rounded-md px-2 text-[11px]"
              >
                完成
              </Button>
            </div>
          )}
        >
          <div className="indicator-screening-parameter-modal">
            {settingsIsScope ? (
              <FormSelect
                label="分组"
                layout="inline"
                labelClassName="w-28"
                ariaLabel="筛选范围分组"
                value={settingsScopeValue}
                options={scopeOptions}
                onChange={(value) => onParameterChange(settingsCondition.id, 'groupId', value || null)}
                density="compact"
                controlClassName="indicator-screening-field"
              />
            ) : (
              <div className="space-y-2">
                {settingsIndicator?.parameterSchema.map((definition) => (
                  <div key={definition.key}>
                    {renderParameter(
                      settingsCondition,
                      definition,
                      (value) => onParameterChange(settingsCondition.id, definition.key, value),
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </Modal>
      ) : null}
    </div>
  );
}

export default IndicatorConditionBuilder;
