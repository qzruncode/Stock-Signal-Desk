import React, { useEffect, useMemo } from 'react';
import { Sparkles } from 'lucide-react';
import type { RssRouteDescriptor } from '../../api/rss';
import { Input, Select, Badge } from '../common';
import {
  parseRouteParams,
  paramsFromExample,
  parseMarkdownParamOptions,
  routeDescriptionProse,
  type RouteParamOption,
} from '../../utils/rssRoute';

export interface RssRouteParamFormProps {
  route: RssRouteDescriptor;
  params: Record<string, string>;
  onParamsChange: (params: Record<string, string>) => void;
}

function FeatureBadges({ features }: { features: RssRouteDescriptor['features'] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {features.requireConfig && <Badge variant="warning">需配置 Cookie/Token</Badge>}
      {features.requirePuppeteer && <Badge variant="info">需 Puppeteer（较慢）</Badge>}
      {features.antiCrawler && <Badge variant="danger">反爬（可能失败）</Badge>}
      {features.supportPodcast && <Badge variant="default">播客</Badge>}
      {features.supportScihub && <Badge variant="default">SciHub</Badge>}
    </div>
  );
}

export const RssRouteParamForm: React.FC<RssRouteParamFormProps> = ({
  route,
  params,
  onParamsChange,
}) => {
  const paramList = useMemo(() => parseRouteParams(route.route_path), [route.route_path]);
  const exampleParams = useMemo(
    () => paramsFromExample(route.route_path, route.example),
    [route.route_path, route.example],
  );
  const tableOptions = useMemo(
    () => parseMarkdownParamOptions(route.description),
    [route.description],
  );
  const description = useMemo(
    () => routeDescriptionProse(route.description),
    [route.description],
  );

  const optionsForParam = (name: string): RouteParamOption[] => {
    const metadata = route.parameters?.[name];
    const metadataDescription = typeof metadata === 'string'
      ? metadata
      : metadata && typeof metadata === 'object' && !Array.isArray(metadata)
        ? String((metadata as { description?: unknown }).description ?? '')
        : '';
    if (metadata && typeof metadata === 'object' && !Array.isArray(metadata)) {
      const rawOptions = (metadata as { options?: unknown }).options;
      if (Array.isArray(rawOptions)) {
        const parsed = rawOptions.flatMap((option): RouteParamOption[] => {
          if (!option || typeof option !== 'object') return [];
          const value = String((option as { value?: unknown }).value ?? '');
          if (!value) return [];
          const label = String((option as { label?: unknown }).label ?? value);
          return [{ value, label }];
        });
        if (parsed.length) return parsed;
      }
    }
    return paramList.length === 1 || /见下表|下表/.test(metadataDescription) ? tableOptions : [];
  };

  // When the route changes, prefill params from the example (one-time per route).
  useEffect(() => {
    if (!paramList.length) return;
    const next: Record<string, string> = {};
    for (const p of paramList) {
      next[p.name] = exampleParams[p.name] ?? '';
    }
    onParamsChange(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.route_path]);

  const update = (name: string, value: string) => {
    onParamsChange({ ...params, [name]: value });
  };

  const fillExample = () => {
    onParamsChange({ ...exampleParams });
  };

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-border bg-muted/30 px-3 py-2">
        <div className="text-[11px] text-muted-text">路由</div>
        <code className="block break-all text-xs text-foreground">{route.route_path}</code>
        {description && (
          <p className="mt-1.5 text-[11px] leading-relaxed text-muted-text">{description}</p>
        )}
        {tableOptions.length > 0 && (
          <p className="mt-1.5 text-[11px] text-muted-text">提供 {tableOptions.length} 个可选分类</p>
        )}
      </div>

      <FeatureBadges features={route.features} />

      {paramList.length === 0 ? (
        <p className="text-xs text-muted-text">此路由无需参数，可直接刷新获取。</p>
      ) : (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-secondary-text">参数</span>
            {route.example && (
              <button
                type="button"
                onClick={fillExample}
                className="inline-flex items-center gap-1 text-[11px] text-cyan hover:underline"
              >
                <Sparkles className="h-3 w-3" />
                用示例填充
              </button>
            )}
          </div>
          {paramList.map((p) => {
            const paramOptions = optionsForParam(p.name);
            const label = `${p.name}${p.optional ? '（可选）' : ''}`;
            return paramOptions.length > 0 ? (
              <Select
                key={p.name}
                label={label}
                value={params[p.name] ?? ''}
                onChange={(value) => update(p.name, value)}
                options={p.optional ? [{ value: '', label: '默认' }, ...paramOptions] : paramOptions}
                placeholder={`选择 ${p.name}`}
              />
            ) : (
              <Input
                key={p.name}
                label={label}
                placeholder={exampleParams[p.name] ? `如 ${exampleParams[p.name]}` : `输入 ${p.name}`}
                value={params[p.name] ?? ''}
                onChange={(e) => update(p.name, e.target.value)}
              />
            );
          })}
        </div>
      )}
    </div>
  );
};

export default RssRouteParamForm;
