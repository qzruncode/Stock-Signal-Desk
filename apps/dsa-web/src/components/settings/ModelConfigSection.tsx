import React, { useMemo, useState } from 'react';
import {
  Lightbulb,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Radio,
} from 'lucide-react';
import { SettingsField } from './SettingsField';
import { TestConnectionButton } from './TestConnectionButton';
import { ProviderSidebar, type ProviderInfo } from './ProviderSidebar';
import type { SystemConfigFieldSchema } from '../../types/systemConfig';

interface ModelConfigSectionProps {
  fieldValues: Record<string, string>;
  fieldSchemas: Record<string, SystemConfigFieldSchema>;
  maskedKeys: Set<string>;
  onChange: (key: string, value: string) => void;
}

interface ProviderGroup {
  key: string;
  title: string;
  fieldKeys: string[];
  description: string;
  testable?: {
    name: string;
    protocol: string;
    apiKeyField: string;
    baseUrlField?: string;
    defaultBaseUrl: string;
    model: string;
  };
}

function resolveTestModel(
  groupProtocol: string,
  fieldValues: Record<string, string>,
  defaultModel: string,
): string {
  const modelFieldMap: Record<string, string> = {
    openai: 'OPENAI_MODEL',
    gemini: 'GEMINI_MODEL',
    anthropic: 'ANTHROPIC_MODEL',
  };
  const modelField = modelFieldMap[groupProtocol];
  if (modelField) {
    const specificModel = (fieldValues[modelField] ?? '').trim();
    if (specificModel) {
      return `${groupProtocol}/${specificModel}`;
    }
  }

  const primaryModel = (fieldValues['LITELLM_MODEL'] ?? '').trim();
  if (primaryModel && primaryModel.startsWith(`${groupProtocol}/`)) {
    return primaryModel;
  }

  return defaultModel;
}

const PROVIDER_GROUPS: ProviderGroup[] = [
  {
    key: 'deepseek',
    title: 'DeepSeek',
    fieldKeys: ['DEEPSEEK_API_KEY', 'DEEPSEEK_API_KEYS'],
    description: 'DeepSeek official API',
    testable: {
      name: 'deepseek',
      protocol: 'deepseek',
      apiKeyField: 'DEEPSEEK_API_KEY',
      defaultBaseUrl: '',
      model: 'deepseek/deepseek-v4-pro',
    },
  },
  {
    key: 'openai',
    title: 'OpenAI',
    fieldKeys: ['OPENAI_API_KEY', 'OPENAI_API_KEYS', 'OPENAI_BASE_URL', 'OPENAI_MODEL', 'OPENAI_TEMPERATURE'],
    description: 'OpenAI and compatible APIs',
    testable: {
      name: 'openai',
      protocol: 'openai',
      apiKeyField: 'OPENAI_API_KEY',
      baseUrlField: 'OPENAI_BASE_URL',
      defaultBaseUrl: 'https://api.openai.com/v1',
      model: 'openai/gpt-5.5',
    },
  },
  {
    key: 'gemini',
    title: 'Google Gemini',
    fieldKeys: ['GEMINI_API_KEY', 'GEMINI_API_KEYS', 'GEMINI_MODEL', 'GEMINI_MODEL_FALLBACK', 'GEMINI_TEMPERATURE'],
    description: 'Google Gemini models',
    testable: {
      name: 'gemini',
      protocol: 'gemini',
      apiKeyField: 'GEMINI_API_KEY',
      defaultBaseUrl: '',
      model: 'gemini/gemini-3.1-pro-preview',
    },
  },
  {
    key: 'anthropic',
    title: 'Anthropic Claude',
    fieldKeys: ['ANTHROPIC_API_KEY', 'ANTHROPIC_API_KEYS', 'ANTHROPIC_MODEL', 'ANTHROPIC_TEMPERATURE', 'ANTHROPIC_MAX_TOKENS'],
    description: 'Anthropic Claude models',
    testable: {
      name: 'anthropic',
      protocol: 'anthropic',
      apiKeyField: 'ANTHROPIC_API_KEY',
      defaultBaseUrl: '',
      model: 'anthropic/claude-sonnet-4-6',
    },
  },
];

export const ModelConfigSection: React.FC<ModelConfigSectionProps> = ({
  fieldValues,
  fieldSchemas,
  maskedKeys,
  onChange,
}) => {
  const [selectedProvider, setSelectedProvider] = useState('main');
  const [advancedOpen, setAdvancedOpen] = useState<Record<string, boolean>>({});

  // Build sidebar provider list
  const sidebarProviders = useMemo<ProviderInfo[]>(() => {
    const checks = [
      { key: 'main', provider: 'Main', keyField: 'LITELLM_MODEL' },
      { key: 'deepseek', provider: 'DeepSeek', keyField: 'DEEPSEEK_API_KEY' },
      { key: 'openai', provider: 'OpenAI', keyField: 'OPENAI_API_KEY' },
      { key: 'gemini', provider: 'Gemini', keyField: 'GEMINI_API_KEY' },
      { key: 'anthropic', provider: 'Anthropic', keyField: 'ANTHROPIC_API_KEY' },
    ];

    return checks.map((check) => {
      const val = fieldValues[check.keyField] ?? '';
      const isMasked = maskedKeys.has(check.keyField);
      const hasValue = (val && val.length >= 8) || isMasked;
      const isActive = check.key === 'main'
        ? !!fieldValues['LITELLM_MODEL']
        : fieldValues['LITELLM_MODEL']?.startsWith(check.key + '/');

      return {
        key: check.key,
        title: check.provider,
        icon: React.createElement('span'), // Icon handled by ProviderSidebar
        status: (isActive && hasValue) ? 'active' : hasValue ? 'configured' : 'not_configured',
      };
    });
  }, [fieldValues, maskedKeys]);

  // Detect active provider hint
  const activeProvider = useMemo(() => {
    const currentModel = (fieldValues['LITELLM_MODEL'] ?? '').trim();
    if (!currentModel) return null;

    for (const group of PROVIDER_GROUPS) {
      if (currentModel.startsWith(group.key + '/')) {
        const keyField = group.testable?.apiKeyField;
        if (keyField) {
          const val = fieldValues[keyField] ?? '';
          const isMasked = maskedKeys.has(keyField);
          if ((val && val.length >= 8) || isMasked) {
            return { provider: group.title, modelPrefix: group.key };
          }
        }
      }
    }
    return null;
  }, [fieldValues, maskedKeys]);

  const currentModel = fieldValues['LITELLM_MODEL'] ?? '';
  const modelMissingPrefix = currentModel !== '' && !currentModel.includes('/');
  const thinkingEnabled = fieldValues['LLM_THINKING_ENABLED'] === 'true';

  const toggleAdvanced = (key: string) => {
    setAdvancedOpen((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const isAdvancedOpen = (key: string) => !!advancedOpen[key];

  // Render main model panel
  const renderMainPanel = () => {
    const litellmField = fieldSchemas['LITELLM_MODEL'];
    const fallbackField = fieldSchemas['LITELLM_FALLBACK_MODELS'];
    const tempField = fieldSchemas['LLM_TEMPERATURE'];
    const thinkingField = fieldSchemas['LLM_THINKING_ENABLED'];
    const effortField = fieldSchemas['LLM_REASONING_EFFORT'];

    return (
      <div className="flex flex-col gap-4">
        {/* Active provider hint */}
        {activeProvider && (
          <div className="rounded-xl border border-cyan/20 bg-cyan/5 px-4 py-2.5 text-sm">
            <div className="flex items-center gap-1.5 font-medium text-foreground">
              <Lightbulb className="h-4 w-4 text-cyan" />
              检测到 {activeProvider.provider} API Key 已配置
            </div>
            <p className="mt-1 text-secondary-text">
              建议设置为 <code className="rounded bg-cyan/10 px-1.5 py-0.5 text-xs text-cyan">{activeProvider.provider.toLowerCase()}/your-model</code>
            </p>
          </div>
        )}

        {modelMissingPrefix && (
          <div className="rounded-xl border border-warning/30 bg-warning/5 px-4 py-2.5 text-sm">
            <div className="flex items-center gap-1.5 font-medium text-warning">
              <AlertTriangle className="h-4 w-4" />
              模型名缺少提供商前缀
            </div>
            <p className="mt-1 text-secondary-text">
              当前值 <code className="rounded bg-warning/10 px-1.5 py-0.5 text-xs text-warning">{currentModel}</code> 缺少 <code className="rounded bg-warning/10 px-1.5 py-0.5 text-xs text-warning">provider/</code> 前缀
            </p>
          </div>
        )}

        {/* Core fields */}
        {litellmField && (
          <SettingsField
            key={litellmField.key}
            field={litellmField}
            value={currentModel}
            onChange={onChange}
          />
        )}

        {fallbackField && (
          <SettingsField
            key={fallbackField.key}
            field={fallbackField}
            value={fieldValues['LITELLM_FALLBACK_MODELS'] ?? ''}
            onChange={onChange}
          />
        )}

        {/* Thinking controls - prominently placed */}
        {thinkingField && (
          <SettingsField
            key={thinkingField.key}
            field={thinkingField}
            value={fieldValues['LLM_THINKING_ENABLED'] ?? 'false'}
            onChange={onChange}
          />
        )}

        {thinkingEnabled && effortField && (
          <SettingsField
            key={effortField.key}
            field={effortField}
            value={fieldValues['LLM_REASONING_EFFORT'] ?? 'auto'}
            onChange={onChange}
          />
        )}

        {tempField && (
          <SettingsField
            key={tempField.key}
            field={tempField}
            value={fieldValues['LLM_TEMPERATURE'] ?? ''}
            onChange={onChange}
          />
        )}

        {/* Format hint */}
        <div className="rounded-xl border border-border/40 bg-surface-1 px-3 py-2 text-xs text-secondary-text">
          <p className="font-medium text-foreground">格式说明</p>
          <p className="mt-1">必须以 <code className="rounded bg-base px-1 py-0.5 text-cyan">provider/model</code> 格式填写：</p>
          <ul className="mt-1.5 space-y-0.5">
            <li><code className="text-cyan">deepseek/deepseek-v4-pro</code> — DeepSeek V4</li>
            <li><code className="text-cyan">openai/gpt-5.5</code> — OpenAI</li>
            <li><code className="text-cyan">gemini/gemini-3.1-pro-preview</code> — Gemini</li>
            <li><code className="text-cyan">anthropic/claude-sonnet-4-6</code> — Claude</li>
          </ul>
        </div>
      </div>
    );
  };

  // Render provider panel
  const renderProviderPanel = (groupKey: string) => {
    const group = PROVIDER_GROUPS.find((g) => g.key === groupKey);
    if (!group) return null;

    const matchedKeys = new Set<string>();
    const groupFields: SystemConfigFieldSchema[] = [];

    for (const key of group.fieldKeys) {
      const schema = fieldSchemas[key];
      if (schema) {
        groupFields.push(schema);
        matchedKeys.add(key);
      }
    }

    const prefix = group.key.toUpperCase() + '_';
    for (const [key, schema] of Object.entries(fieldSchemas)) {
      if (matchedKeys.has(key)) continue;
      if (key.startsWith(prefix)) {
        groupFields.push(schema);
        matchedKeys.add(key);
      }
    }

    groupFields.sort((a, b) => (a.displayOrder ?? 9000) - (b.displayOrder ?? 9000));

    // Split into core and advanced fields
    const coreKeys = new Set([
      group.testable?.apiKeyField,
      group.key === 'openai' ? 'OPENAI_MODEL' : group.key === 'gemini' ? 'GEMINI_MODEL' : group.key === 'anthropic' ? 'ANTHROPIC_MODEL' : '',
      group.key === 'deepseek' ? 'DEEPSEEK_API_KEYS' : '',
      group.key === 'openai' ? 'OPENAI_API_KEYS' : '',
      group.key === 'gemini' ? 'GEMINI_API_KEYS' : '',
      group.key === 'anthropic' ? 'ANTHROPIC_API_KEYS' : '',
      group.key === 'gemini' ? 'GEMINI_MODEL_FALLBACK' : '',
    ].filter(Boolean));

    const coreFields = groupFields.filter((f) => coreKeys.has(f.key));
    const advancedFields = groupFields.filter((f) => !coreKeys.has(f.key));

    const apiKeyValue = group.testable
      ? (fieldValues[group.testable.apiKeyField] ?? '')
      : '';
    const baseUrlValue = group.testable?.baseUrlField
      ? (fieldValues[group.testable.baseUrlField] ?? '')
      : (group.testable?.defaultBaseUrl ?? '');

    const hasConfiguredField = coreFields.some((f) => {
      const val = fieldValues[f.key];
      return (val && val !== '') || maskedKeys.has(f.key);
    });

    const resolvedModel = group.testable
      ? resolveTestModel(group.testable.protocol, fieldValues, group.testable.model)
      : '';

    return (
      <div className="flex flex-col gap-4">
        {/* Description */}
        <p className="text-sm text-secondary-text">{group.description}</p>

        {/* Core fields */}
        {coreFields.map((fieldSchema) => (
          <SettingsField
            key={fieldSchema.key}
            field={fieldSchema}
            value={fieldValues[fieldSchema.key] ?? ''}
            onChange={onChange}
            isMasked={maskedKeys.has(fieldSchema.key)}
          />
        ))}

        {/* Advanced fields collapsible */}
        {advancedFields.length > 0 && (
          <div className="rounded-xl border border-border/40 bg-surface-1/50">
            <button
              type="button"
              onClick={() => toggleAdvanced(groupKey)}
              className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-medium text-secondary-text transition hover:text-foreground"
            >
              {isAdvancedOpen(groupKey) ? (
                <ChevronDown className="h-4 w-4" />
              ) : (
                <ChevronRight className="h-4 w-4" />
              )}
              高级设置
            </button>
            {isAdvancedOpen(groupKey) && (
              <div className="border-t border-border/40 px-4 py-3">
                <div className="flex flex-col gap-4">
                  {advancedFields.map((fieldSchema) => (
                    <SettingsField
                      key={fieldSchema.key}
                      field={fieldSchema}
                      value={fieldValues[fieldSchema.key] ?? ''}
                      onChange={onChange}
                      isMasked={maskedKeys.has(fieldSchema.key)}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Test connection */}
        {group.testable && (
          <TestConnectionButton
            name={group.testable.name}
            protocol={group.testable.protocol}
            apiKey={apiKeyValue}
            baseUrl={baseUrlValue}
            models={hasConfiguredField ? [resolvedModel] : []}
            disabled={!hasConfiguredField}
          />
        )}
      </div>
    );
  };

  const panelKey = selectedProvider === 'main' ? 'main' : selectedProvider;

  return (
    <div className="flex flex-col gap-6 md:flex-row">
      {/* Left sidebar */}
      <aside className="md:w-52 md:shrink-0">
        <ProviderSidebar
          providers={sidebarProviders}
          selectedKey={selectedProvider}
          onSelect={setSelectedProvider}
        />
      </aside>

      {/* Right panel */}
      <div className="min-w-0 flex-1">
        <div className="rounded-xl border border-border/40 bg-surface-1/30 px-5 py-4">
          <div className="mb-4 flex items-center gap-2">
            {panelKey === 'main' ? (
              <Radio className="h-4 w-4 text-cyan" />
            ) : (
              PROVIDER_GROUPS.find((g) => g.key === panelKey)?.title && (
                <span className="text-sm font-semibold text-foreground">
                  {PROVIDER_GROUPS.find((g) => g.key === panelKey)?.title}
                </span>
              )
            )}
            {panelKey === 'main' && (
              <span className="text-sm font-semibold text-foreground">主模型配置</span>
            )}
          </div>

          {panelKey === 'main' && renderMainPanel()}
          {panelKey !== 'main' && renderProviderPanel(panelKey)}
        </div>
      </div>
    </div>
  );
};
