import React, { useEffect, useMemo, useState } from 'react';
import { Sparkles, ShieldCheck } from 'lucide-react';
import { rssApi, type RssRouteDescriptor, type RssCookieTestResult } from '../../api/rss';
import { Input, Select, Badge, Button, InlineAlert } from '../common';
import {
  parseRouteParams,
  paramsFromExample,
  parseMarkdownParamOptions,
  routeDescriptionProse,
  requiresAuth,
  type RouteParamOption,
} from '../../utils/rssRoute';

export interface RssRouteParamFormProps {
  route: RssRouteDescriptor;
  params: Record<string, string>;
  onParamsChange: (params: Record<string, string>) => void;
}

/**
 * "需配置 Cookie" 路由的提示 + Cookie 生效测试。
 *
 * XUEQIU_COOKIES 是 RSSHub 实例级环境变量（含 HttpOnly 的 xq_a_token），页面无法
 * 直接配置——只能提示联系管理员。这里提供一个"测试 Cookie 是否生效"按钮，管理员
 * 配好 .env 并重启实例后，用户点此确认实际取数是否正常。
 */
function CookieTestSection() {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<RssCookieTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runTest = async () => {
    setTesting(true);
    setError(null);
    setResult(null);
    try {
      const res = await rssApi.testXueqiuCookie();
      setResult(res);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: { message?: string } } } })
        ?.response?.data?.detail?.message
        || (err as Error).message
        || '测试失败';
      setError(msg);
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="rounded-lg border border-warning/30 bg-warning/5 px-3 py-2.5 space-y-2">
      <div className="flex items-start gap-1.5 text-[11px] leading-relaxed text-secondary-text">
        <span>此路由需登录态 Cookie（含 HttpOnly 的 <code className="text-foreground">xq_a_token</code>），由管理员在 RSSHub 实例 <code className="text-foreground">.env</code> 配置 <code className="text-foreground">XUEQIU_COOKIES</code> 并重启实例后生效。页面无法直接配置，如需使用请联系管理员。</span>
      </div>
      <Button variant="outline" size="sm" isLoading={testing} onClick={() => void runTest()}>
        <ShieldCheck className="h-3.5 w-3.5" />
        测试 Cookie 是否生效
      </Button>
      {error && <InlineAlert title="测试失败" variant="danger" message={error} />}
      {result && (
        <InlineAlert
          title={result.verified ? 'Cookie 已生效' : 'Cookie 未生效'}
          variant={result.verified ? 'success' : 'warning'}
          message={result.message}
        />
      )}
    </div>
  );
}

function FeatureBadges({ route }: { route: RssRouteDescriptor }) {
  const { features } = route;
  const authReason = requiresAuth(route);
  return (
    <div className="flex flex-wrap gap-1.5">
      {authReason && <Badge variant="warning">{authReason}</Badge>}
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

      <FeatureBadges route={route} />
      {requiresAuth(route) && <CookieTestSection />}

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
