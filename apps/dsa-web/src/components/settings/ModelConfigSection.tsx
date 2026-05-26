import React, { useMemo, useState } from 'react';
import { Sparkles, Cpu, Globe, AlertTriangle, Lightbulb, ChevronDown, ChevronRight } from 'lucide-react';
import { Card } from '../common/Card';
import { Badge } from '../common/Badge';
import { SettingsField } from './SettingsField';
import { TestConnectionButton } from './TestConnectionButton';
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
  icon: React.ReactNode;
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

interface CollapsibleConfigCardProps {
  title: string;
  subtitle: string;
  children: React.ReactNode;
  status?: React.ReactNode;
}

const CollapsibleConfigCard: React.FC<CollapsibleConfigCardProps> = ({
  title,
  subtitle,
  children,
  status,
}) => {
  const [expanded, setExpanded] = useState(false);

  return (
    <Card variant="default" padding="none" className="overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-start gap-3 px-5 py-4 text-left transition hover:bg-hover/40"
        aria-expanded={expanded}
      >
        <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-lg border border-border/60 bg-surface-1 text-secondary-text">
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-base font-semibold text-foreground">{title}</span>
          <span className="mt-1 block text-xs leading-5 text-secondary-text">{subtitle}</span>
        </span>
        {status ? <span className="shrink-0">{status}</span> : null}
      </button>
      {expanded ? <div className="border-t border-border/50 px-5 py-4">{children}</div> : null}
    </Card>
  );
};

const PROVIDER_GROUPS: ProviderGroup[] = [
  {
    key: 'deepseek',
    title: 'DeepSeek',
    icon: <Cpu className="h-4 w-4" />,
    fieldKeys: ['DEEPSEEK_API_KEY', 'DEEPSEEK_API_KEYS'],
    description: 'DeepSeek official API (api.deepseek.com). Enter your API key below, then set LITELLM_MODEL to deepseek/<your-model>.',
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
    icon: <Globe className="h-4 w-4" />,
    fieldKeys: ['OPENAI_API_KEY', 'OPENAI_API_KEYS', 'OPENAI_BASE_URL', 'OPENAI_MODEL', 'OPENAI_TEMPERATURE'],
    description: 'OpenAI and OpenAI-compatible APIs.',
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
    icon: <Sparkles className="h-4 w-4 text-amber-400" />,
    fieldKeys: ['GEMINI_API_KEY', 'GEMINI_API_KEYS', 'GEMINI_MODEL', 'GEMINI_MODEL_FALLBACK', 'GEMINI_TEMPERATURE'],
    description: 'Google Gemini models.',
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
    icon: <Cpu className="h-4 w-4 text-orange-400" />,
    fieldKeys: ['ANTHROPIC_API_KEY', 'ANTHROPIC_API_KEYS', 'ANTHROPIC_MODEL', 'ANTHROPIC_TEMPERATURE', 'ANTHROPIC_MAX_TOKENS'],
    description: 'Anthropic Claude models.',
    testable: {
      name: 'anthropic',
      protocol: 'anthropic',
      apiKeyField: 'ANTHROPIC_API_KEY',
      defaultBaseUrl: '',
      model: 'anthropic/claude-sonnet-4-6',
    },
  },
];

function resolveTestModel(
  groupProtocol: string,
  fieldValues: Record<string, string>,
  defaultModel: string,
): string {
  // Use provider-specific model field if set
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

  // Fall back to LITELLM_MODEL if it matches this protocol
  const primaryModel = (fieldValues['LITELLM_MODEL'] ?? '').trim();
  if (primaryModel && primaryModel.startsWith(`${groupProtocol}/`)) {
    return primaryModel;
  }

  return defaultModel;
}

function detectActiveProvider(fieldValues: Record<string, string>, maskedKeys: Set<string>): {
  provider: string;
  modelPrefix: string;
  suggestedModel: string;
} | null {
  // Check each provider's API key
  const checks: Array<{ provider: string; keyField: string; modelPrefix: string; defaultModel: string }> = [
    { provider: 'DeepSeek', keyField: 'DEEPSEEK_API_KEY', modelPrefix: 'deepseek/', defaultModel: 'deepseek/deepseek-v4-pro' },
    { provider: 'OpenAI', keyField: 'OPENAI_API_KEY', modelPrefix: 'openai/', defaultModel: 'openai/gpt-5.5' },
    { provider: 'Gemini', keyField: 'GEMINI_API_KEY', modelPrefix: 'gemini/', defaultModel: 'gemini/gemini-3.1-pro-preview' },
    { provider: 'Anthropic', keyField: 'ANTHROPIC_API_KEY', modelPrefix: 'anthropic/', defaultModel: 'anthropic/claude-sonnet-4-6' },
  ];

  for (const check of checks) {
    const val = fieldValues[check.keyField] ?? '';
    const isMasked = maskedKeys.has(check.keyField);
    if ((val && val.length >= 8) || isMasked) {
      return { provider: check.provider, modelPrefix: check.modelPrefix, suggestedModel: check.defaultModel };
    }
  }
  return null;
}

export const ModelConfigSection: React.FC<ModelConfigSectionProps> = ({
  fieldValues,
  fieldSchemas,
  maskedKeys,
  onChange,
}) => {
  const activeProvider = useMemo(() => detectActiveProvider(fieldValues, maskedKeys), [fieldValues, maskedKeys]);

  const currentModel = fieldValues['LITELLM_MODEL'] ?? '';
  const modelMissingPrefix = currentModel !== '' && !currentModel.includes('/');

  const groupsWithFields = useMemo(() => {
    const matchedKeys = new Set<string>();

    return PROVIDER_GROUPS.map((group) => {
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
        if (key.startsWith(prefix) && !group.fieldKeys.includes(key)) {
          groupFields.push(schema);
          matchedKeys.add(key);
        }
      }

      groupFields.sort((a, b) => (a.displayOrder ?? 9000) - (b.displayOrder ?? 9000));

      const hasValues = groupFields.some((f) => {
        const val = fieldValues[f.key];
        return val && val !== '' && !maskedKeys.has(f.key);
      });
      const hasMaskedValues = groupFields.some((f) => maskedKeys.has(f.key));

      return {
        key: group.key,
        title: group.title,
        icon: group.icon,
        description: group.description,
        fields: groupFields,
        testable: group.testable,
        status: hasValues || hasMaskedValues ? 'configured' as const : 'not_configured' as const,
      };
    });
  }, [fieldSchemas, fieldValues, maskedKeys]);

  return (
    <div className="flex flex-col gap-4">
      {/* LITELLM_MODEL main config card */}
      {fieldSchemas['LITELLM_MODEL'] && (
        <CollapsibleConfigCard
          title="主模型 (LITELLM_MODEL)"
          subtitle="这个字段决定实际调用哪个 AI 模型。格式必须是 提供商/模型名。"
        >
          <div className="flex flex-col gap-3">
            {activeProvider && (
              <div className="rounded-xl border border-cyan/20 bg-cyan/5 px-4 py-2.5 text-sm">
                <div className="flex items-center gap-1.5 font-medium text-foreground">
                  <Lightbulb className="h-4 w-4 text-cyan" />
                  检测到 {activeProvider.provider} API Key 已配置
                </div>
                <p className="mt-1 text-secondary-text">
                  建议设置为 <code className="rounded bg-cyan/10 px-1.5 py-0.5 text-xs text-cyan">{activeProvider.suggestedModel}</code>
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
                  当前值 <code className="rounded bg-warning/10 px-1.5 py-0.5 text-xs text-warning">{currentModel}</code> 缺少 <code className="rounded bg-warning/10 px-1.5 py-0.5 text-xs text-warning">provider/</code> 前缀，
                  请改为类似 <code className="rounded bg-cyan/10 px-1.5 py-0.5 text-xs text-cyan">deepseek/{currentModel}</code> 的格式。
                </p>
              </div>
            )}

            <SettingsField
              key={fieldSchemas['LITELLM_MODEL'].key}
              field={fieldSchemas['LITELLM_MODEL']}
              value={currentModel}
              onChange={onChange}
            />

            <div className="rounded-xl border border-border/40 bg-surface-1 px-3 py-2 text-xs text-secondary-text">
              <p className="font-medium text-foreground">格式说明</p>
              <p className="mt-1">必须以 <code className="rounded bg-base px-1 py-0.5 text-cyan">provider/model</code> 格式填写：</p>
              <ul className="mt-1.5 space-y-0.5">
                <li><code className="text-cyan">deepseek/deepseek-v4-pro</code> — DeepSeek V4</li>
                <li><code className="text-cyan">deepseek/deepseek-chat</code> — DeepSeek V3</li>
                <li><code className="text-cyan">openai/gpt-5.5</code> — OpenAI</li>
                <li><code className="text-cyan">gemini/gemini-3.1-pro-preview</code> — Gemini</li>
                <li><code className="text-cyan">anthropic/claude-sonnet-4-6</code> — Claude</li>
              </ul>
            </div>

            {fieldSchemas['LITELLM_FALLBACK_MODELS'] && (
              <SettingsField
                key={fieldSchemas['LITELLM_FALLBACK_MODELS'].key}
                field={fieldSchemas['LITELLM_FALLBACK_MODELS']}
                value={fieldValues['LITELLM_FALLBACK_MODELS'] ?? ''}
                onChange={onChange}
              />
            )}

            {fieldSchemas['LLM_TEMPERATURE'] && (
              <SettingsField
                key={fieldSchemas['LLM_TEMPERATURE'].key}
                field={fieldSchemas['LLM_TEMPERATURE']}
                value={fieldValues['LLM_TEMPERATURE'] ?? ''}
                onChange={onChange}
              />
            )}
          </div>
        </CollapsibleConfigCard>
      )}

      {/* Provider cards */}
      {groupsWithFields.map((group) => {
        if (group.fields.length === 0) return null;

        const apiKeyValue = group.testable
          ? (fieldValues[group.testable.apiKeyField] ?? '')
          : '';
        const baseUrlValue = group.testable?.baseUrlField
          ? (fieldValues[group.testable.baseUrlField] ?? '')
          : (group.testable?.defaultBaseUrl ?? '');

        return (
          <CollapsibleConfigCard
            key={group.key}
            title={group.title}
            subtitle={group.description}
            status={(
              <Badge
                variant={group.status === 'configured' ? 'success' : 'default'}
                size="sm"
              >
                {group.status === 'configured' ? '已配置' : '未配置'}
              </Badge>
            )}
          >
            <div className="mb-3 flex items-center gap-2">
              {group.icon}
              <span className="text-sm font-medium text-foreground">{group.title}</span>
            </div>
            <div className="flex flex-col gap-4">
              {group.fields.map((fieldSchema) => (
                <SettingsField
                  key={fieldSchema.key}
                  field={fieldSchema}
                  value={fieldValues[fieldSchema.key] ?? ''}
                  onChange={onChange}
                  isMasked={maskedKeys.has(fieldSchema.key)}
                />
              ))}

              {group.testable && (() => {
                const resolvedModel = resolveTestModel(group.testable.protocol, fieldValues, group.testable.model);
                return (
                <TestConnectionButton
                  name={group.testable.name}
                  protocol={group.testable.protocol}
                  apiKey={apiKeyValue}
                  baseUrl={baseUrlValue}
                  models={[resolvedModel]}
                  disabled={group.status !== 'configured'}
                />
                );
              })()}
            </div>
          </CollapsibleConfigCard>
        );
      })}
    </div>
  );
};
