import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import { FileTextIcon, RadioIcon } from 'lucide-react';
import type { FeedSpec, RssFeedOptions, RssItem } from '../../../api/rss';
import RssDownloadMenu from '../../rss/RssDownloadMenu';
import RssFeedList from '../../rss/RssFeedList';
import { ToolStatusPill } from './shared';

type ToolResultBase = {
  success?: boolean;
  errors?: string[];
  warnings?: string[];
};

type FeedToolResult = ToolResultBase & {
  route_path?: string;
  namespace?: string;
  params?: Record<string, unknown>;
  options?: Record<string, unknown>;
  feed_title?: string;
  item_count?: number;
  items?: Array<Partial<RssItem>>;
};

type SourceItem = {
  route_path?: string;
  name?: string;
  namespace?: string;
  namespace_name?: string;
  description?: string;
  capabilities?: string[];
  categories?: string[];
  features?: Record<string, boolean>;
  maintainers?: string[];
  requires_configuration?: boolean;
  params?: Array<{ name?: string; required?: boolean; hint?: string; default?: string | null; options?: unknown[] }>;
};

type SourcesToolResult = ToolResultBase & {
  catalog_count?: number;
  matched_count?: number;
  item_count?: number;
  returned_count?: number;
  has_more?: boolean;
  items?: SourceItem[];
  route?: SourceItem;
  dynamic_options?: Record<string, unknown> | null;
  readiness?: { configured?: boolean; verified?: boolean; message?: string } | null;
  query_scope?: string;
  query_note?: string;
  applied_filters?: { keyword?: string; namespace?: string; capability?: string };
};

type ArticleToolResult = ToolResultBase & {
  title?: string;
  link?: string;
  published?: string | null;
  author?: string;
  content_text?: string;
  content_length?: number;
  offset?: number;
  next_offset?: number | null;
  has_more?: boolean;
  image?: string;
  attachments?: Array<{ url?: string; title?: string; mime_type?: string }>;
  tags?: string[];
  route_path?: string;
  namespace?: string;
  params?: Record<string, unknown>;
  options?: Record<string, unknown>;
};

type ExportToolResult = ToolResultBase & {
  route_path?: string;
  namespace?: string;
  params?: Record<string, unknown>;
  options?: Record<string, unknown>;
  format?: string;
  limit?: number;
};

function failed(status: ToolCallMessagePartProps['status'], isError?: boolean, result?: ToolResultBase): boolean {
  return Boolean(isError || (status.type === 'incomplete' && status.reason === 'error') || result?.success === false);
}

function stringRecord(value?: Record<string, unknown>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(value ?? {}).filter(([, item]) => item != null).map(([key, item]) => [key, String(item)]),
  );
}

function feedSpec(result?: FeedToolResult | ExportToolResult | ArticleToolResult): FeedSpec | null {
  if (!result?.route_path) return null;
  return {
    route_path: result.route_path,
    namespace: result.namespace ?? '',
    params: stringRecord(result.params),
    options: (result.options ?? {}) as RssFeedOptions,
  };
}

function normalizeItem(item: Partial<RssItem>, index: number): RssItem {
  return {
    id: item.id || item.link || `${item.title || 'item'}-${index}`,
    title: item.title || '消息详情',
    link: item.link || '',
    summary: item.summary || '',
    published: item.published || null,
    author: item.author || '',
    tags: item.tags || [],
    image: item.image,
    content_html: item.content_html,
    attachments: item.attachments || [],
  };
}

export function FinancialFeedToolUI({
  toolName,
  result,
  status,
  isError,
}: ToolCallMessagePartProps<Record<string, unknown>, FeedToolResult | undefined>) {
  const transforming = toolName === 'transform_webpage_to_feed';
  if (status.type === 'running' && !result) {
    return <ToolStatusPill status={status} label={transforming ? '正在把网页转换为资讯流' : '正在读取指定资讯源'} />;
  }
  if (failed(status, isError, result)) {
    return <ToolStatusPill status={status} isError label={transforming ? '网页转换失败' : '资讯源读取失败'} />;
  }
  const items = (result?.items ?? []).map(normalizeItem);
  if (items.length === 0) {
    return <ToolStatusPill status={status} label="资讯源读取完成，本次没有消息" />;
  }
  const spec = transforming ? null : feedSpec(result);
  return (
    <div className="my-2 min-w-0 space-y-3 rounded-xl border border-border bg-card/60 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs font-semibold text-foreground">{result?.feed_title || (transforming ? '网页资讯流' : '指定资讯源')}</p>
          {result?.route_path && <p className="mt-0.5 break-all text-[10px] text-muted-foreground">{result.route_path}</p>}
        </div>
        {spec && <RssDownloadMenu spec={spec} />}
      </div>
      <RssFeedList items={items} feedTitle={result?.feed_title} spec={spec} />
    </div>
  );
}

type DynamicChoice = { label: string; value: string; detail?: string };

function recordText(value: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const item = value[key];
    if (item != null && String(item).trim()) return String(item).trim();
  }
  return '';
}

function dynamicChoices(value?: Record<string, unknown> | null): DynamicChoice[] {
  if (!value) return [];
  const rows = Object.values(value).find(Array.isArray) as unknown[] | undefined;
  if (!rows) return [];
  const choices: DynamicChoice[] = [];
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue;
    const item = row as Record<string, unknown>;
    const children = Array.isArray(item.children) ? item.children : [];
    if (children.length > 0) {
      for (const child of children) {
        if (!child || typeof child !== 'object') continue;
        const childItem = child as Record<string, unknown>;
        const parentName = recordText(item, ['name', 'title', 'className', 'type']);
        const childName = recordText(childItem, ['name', 'title', 'className', 'type']);
        choices.push({
          label: [parentName, childName].filter(Boolean).join(' / '),
          value: [recordText(item, ['type', 'id']), recordText(childItem, ['type', 'id'])].filter(Boolean).join('/'),
        });
      }
      continue;
    }
    choices.push({
      label: recordText(item, ['name', 'title', 'className', 'detail', 'subjectId', 'topicId', 'classId', 'type']),
      value: recordText(item, ['subjectId', 'topicId', 'classId', 'id', 'type', 'idx']),
      detail: recordText(item, ['summary', 'detail', 'link']),
    });
  }
  return choices.filter((choice) => choice.label || choice.value);
}

export function FinancialSourcesToolUI({
  toolName,
  result,
  status,
  isError,
}: ToolCallMessagePartProps<Record<string, unknown>, SourcesToolResult | undefined>) {
  const inspecting = toolName === 'inspect_financial_source';
  if (status.type === 'running' && !result) {
    return <ToolStatusPill status={status} label={inspecting ? '正在检查资讯源' : '正在整理可用资讯源'} />;
  }
  if (failed(status, isError, result)) {
    return <ToolStatusPill status={status} isError label={inspecting ? '资讯源检查失败' : '资讯源目录获取失败'} />;
  }
  if (inspecting && result?.route) {
    const route = result.route;
    const choices = dynamicChoices(result.dynamic_options);
    return (
      <div className="my-2 rounded-xl border border-border bg-card/60 p-3 text-xs">
        <div className="flex items-center gap-2 font-semibold text-foreground"><RadioIcon className="size-4 text-cyan-600" />{route.name || route.route_path}</div>
        {route.description && <p className="mt-1.5 leading-5 text-muted-foreground">{route.description}</p>}
        <div className="mt-2 flex flex-wrap gap-2 text-[10px] text-muted-foreground">
          {route.route_path && <span className="rounded bg-muted px-2 py-1">{route.route_path}</span>}
          {route.capabilities?.map((capability) => <span key={capability} className="rounded bg-muted px-2 py-1">{capability}</span>)}
          {route.requires_configuration && <span className="rounded bg-amber-100 px-2 py-1 text-amber-700">需配置 Cookie/Token</span>}
          {result.readiness?.message && <span className="rounded bg-muted px-2 py-1">{result.readiness.message}</span>}
        </div>
        {route.params && route.params.length > 0 && (
          <div className="mt-3 space-y-1.5">
            <p className="font-medium text-foreground">路由参数</p>
            {route.params.map((param, index) => (
              <div key={`${param.name}-${index}`} className="rounded-md bg-muted/45 px-2.5 py-2 text-[10px] text-muted-foreground">
                <span className="font-medium text-foreground">{param.name}</span>
                <span> · {param.required ? '必填' : '可选'}</span>
                {param.default != null && <span> · 默认 {param.default}</span>}
                {param.hint && <p className="mt-1 leading-4">{param.hint}</p>}
              </div>
            ))}
          </div>
        )}
        {choices.length > 0 && (
          <div className="mt-3">
            <p className="mb-1.5 font-medium text-foreground">动态选项 · {choices.length} 个</p>
            <div className="max-h-72 space-y-1 overflow-y-auto rounded-lg border border-border p-1.5">
              {choices.map((choice, index) => (
                <div key={`${choice.value}-${index}`} className="rounded-md px-2 py-1.5 hover:bg-muted/50">
                  <span className="font-medium text-foreground">{choice.label || choice.value}</span>
                  {choice.value && choice.value !== choice.label && <span className="ml-2 text-muted-foreground">{choice.value}</span>}
                  {choice.detail && <p className="mt-0.5 line-clamp-2 text-[10px] leading-4 text-muted-foreground">{choice.detail}</p>}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }
  const items = result?.items ?? [];
  const grouped = items.reduce<Record<string, SourceItem[]>>((groups, item) => {
    const key = item.namespace_name || item.namespace || '其他来源';
    (groups[key] ??= []).push(item);
    return groups;
  }, {});
  return (
    <div className="my-2 space-y-2 rounded-xl border border-border bg-card/60 p-3">
      <div className="text-xs font-semibold text-foreground">
        资讯源目录 {result?.catalog_count ?? result?.item_count ?? items.length} 个
        {' · '}匹配 {result?.matched_count ?? result?.item_count ?? items.length} 个
        {' · '}返回 {result?.returned_count ?? items.length} 个
      </div>
      {result?.query_note && <p className="text-[10px] leading-4 text-muted-foreground">{result.query_note}</p>}
      {items.length === 0 && (
        <p className="rounded-lg bg-muted/40 px-3 py-4 text-center text-xs text-muted-foreground">
          来源目录中没有匹配项。如需查找“{result?.applied_filters?.keyword || '该关键词'}”相关资讯，请使用财经资讯搜索。
        </p>
      )}
      <div className="max-h-[34rem] space-y-2 overflow-y-auto pr-1">
        {Object.entries(grouped).map(([namespace, sourceItems]) => (
          <details key={namespace} open className="rounded-lg border border-border bg-muted/20">
            <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-foreground">{namespace} · {sourceItems.length}</summary>
            <div className="grid gap-2 border-t border-border p-2 sm:grid-cols-2">
              {sourceItems.map((item, index) => (
                <div key={item.route_path || index} className="min-w-0 rounded-lg bg-card p-2.5">
                  <p className="text-xs font-medium text-foreground">{item.name || item.route_path}</p>
                  <p className="mt-0.5 break-all text-[10px] text-muted-foreground">{item.route_path}</p>
                  {item.description && <p className="mt-1 text-[10px] leading-4 text-muted-foreground">{item.description}</p>}
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {item.capabilities?.map((capability) => <span key={capability} className="rounded bg-muted px-1.5 py-0.5 text-[9px] text-muted-foreground">{capability}</span>)}
                    {item.requires_configuration && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[9px] text-amber-700">需配置</span>}
                  </div>
                </div>
              ))}
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}

export function FinancialArticleToolUI({
  result,
  status,
  isError,
}: ToolCallMessagePartProps<Record<string, unknown>, ArticleToolResult | undefined>) {
  if (status.type === 'running' && !result) return <ToolStatusPill status={status} label="正在读取资讯全文" />;
  if (failed(status, isError, result)) return <ToolStatusPill status={status} isError label="资讯全文读取失败" />;
  const item: RssItem = {
    id: result?.link || result?.title || 'article',
    title: result?.title || '消息全文',
    link: result?.link || '',
    summary: result?.content_text || '',
    published: result?.published || null,
    author: result?.author || '',
    tags: result?.tags || [],
    image: result?.image,
    attachments: (result?.attachments ?? []).flatMap((attachment) => attachment.url ? [{
      url: attachment.url,
      title: attachment.title,
      mime_type: attachment.mime_type || '',
    }] : []),
  };
  return (
    <article className="my-2 space-y-2 rounded-xl border border-border bg-card/60 p-3">
      <div className="flex items-center gap-2 text-xs font-semibold text-foreground">
        <FileTextIcon className="size-4 text-cyan-600" />应用内全文阅读
      </div>
      <RssFeedList items={[item]} spec={feedSpec(result)} />
      {result?.has_more && <p className="mt-3 text-[10px] text-amber-600">正文尚未读完，助手会从 {result.next_offset} 继续读取。</p>}
    </article>
  );
}

export function FinancialExportToolUI({
  result,
  status,
  isError,
}: ToolCallMessagePartProps<Record<string, unknown>, ExportToolResult | undefined>) {
  if (status.type === 'running' && !result) return <ToolStatusPill status={status} label="正在准备资讯下载" />;
  if (failed(status, isError, result)) return <ToolStatusPill status={status} isError label="资讯下载准备失败" />;
  const spec = feedSpec(result);
  return (
    <div className="my-2 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card/60 p-3">
      <div className="min-w-0">
        <p className="text-xs font-semibold text-foreground">资讯订阅已可下载</p>
        <p className="mt-0.5 break-all text-[10px] text-muted-foreground">{result?.route_path} · 推荐 {result?.format?.toUpperCase()}</p>
      </div>
      <RssDownloadMenu spec={spec} />
    </div>
  );
}
