import { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { Badge } from '../common';
import type { ToolCategory, ToolMeta } from '../../types/toolRegistry';
import { useToolTest } from '../../hooks/useToolTest';
import { ToolParamInput } from './ToolParamInput';
import { ToolTestPanel } from './ToolTestPanel';

const CATEGORY_LABEL: Record<ToolCategory, string> = {
  data: '数据',
  market: '市场',
  financials: '财务',
  sentiment: '舆情',
  macro: '宏观',
  search: '联网',
  analysis: '分析',
  research: '研究',
  regulatory: '监管',
  events: '公告',
  risk: '风险',
};

const CATEGORY_VARIANT = {
  data: 'info' as const,
  market: 'info' as const,
  financials: 'history' as const,
  sentiment: 'warning' as const,
  macro: 'success' as const,
  search: 'default' as const,
  analysis: 'history' as const,
  research: 'history' as const,
  regulatory: 'warning' as const,
  events: 'info' as const,
  risk: 'warning' as const,
};

function formatDefault(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

interface ToolListItemProps {
  tool: ToolMeta;
}

export const ToolListItem: React.FC<ToolListItemProps> = ({ tool }) => {
  const [open, setOpen] = useState(false);
  const test = useToolTest(tool);
  const categoryVariant = CATEGORY_VARIANT[tool.category] ?? 'default';
  const Chevron = open ? ChevronUp : ChevronDown;
  const running = test.status === 'running';

  return (
    <div
      className={
        'rounded-xl border border-border/55 bg-card/60 transition ' +
        (open ? 'bg-card shadow-soft-card' : 'hover:bg-card')
      }
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors active:bg-hover/40"
      >
        <code className="min-w-0 flex-1 truncate rounded-md bg-elevated/60 px-2 py-1 font-mono text-sm text-foreground">
          {tool.name}
        </code>
        <Badge variant={categoryVariant} size="sm">
          {CATEGORY_LABEL[tool.category] ?? tool.category}
        </Badge>
        <span className="hidden truncate text-xs text-secondary-text md:block md:max-w-[40%]">
          {tool.description}
        </span>
        <Chevron
          className={
            'h-4 w-4 shrink-0 transition-all duration-300 ease-out ' +
            (open ? 'rotate-180 text-cyan' : 'rotate-0 text-muted-foreground')
          }
        />
      </button>

      <div
        className="grid border-t border-border/40 transition-[grid-template-rows] duration-300 ease-out"
        style={{ gridTemplateRows: open ? '1fr' : '0fr' }}
        aria-hidden={!open}
      >
        <div className="overflow-hidden">
          <div
            className={
              'px-4 py-3 transition-opacity duration-300 ease-out ' +
              (open ? 'opacity-100' : 'opacity-0')
            }
          >
            <p className="whitespace-pre-line text-sm leading-relaxed text-secondary-text">
              {tool.description}
            </p>

            {tool.parameters.length > 0 ? (
              <div className="mt-3">
                <p className="label-uppercase mb-2">参数</p>
                <ul className="space-y-2">
                  {tool.parameters.map((param) => {
                    const defaultText = formatDefault(param.default);
                    return (
                      <li key={param.name} className="space-y-1">
                        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs">
                          <code className="rounded bg-elevated/75 px-1.5 py-0.5 font-mono text-foreground/90">
                            {param.name}
                          </code>
                          <span className="text-muted-foreground">{param.type}</span>
                          {param.required ? (
                            <span className="rounded-sm bg-danger/15 px-1 text-[10px] font-medium text-danger">
                              必填
                            </span>
                          ) : null}
                          {param.description ? (
                            <span className="text-muted-foreground">— {param.description}</span>
                          ) : null}
                          {Array.isArray(param.enum) && param.enum.length > 0 ? (
                            <span className="text-muted-foreground">
                              [可选: {param.enum.map((value) => formatDefault(value)).join(' | ')}]
                            </span>
                          ) : null}
                          {defaultText !== '' ? (
                            <span className="text-muted-foreground">默认: {defaultText}</span>
                          ) : null}
                        </div>
                        <ToolParamInput
                          param={param}
                          value={test.values[param.name] ?? ''}
                          onChange={(v) => test.setValue(param.name, v)}
                          disabled={running}
                        />
                      </li>
                    );
                  })}
                </ul>
              </div>
            ) : (
              <p className="mt-3 text-xs text-muted-foreground">无参数</p>
            )}

            <div className="mt-4 border-t border-border/40 pt-3">
              <ToolTestPanel test={test} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ToolListItem;
