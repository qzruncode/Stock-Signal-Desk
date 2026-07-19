import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { FinancialSourcesToolUI } from './FinancialNewsToolsUI';

const completed = { type: 'complete' as const };
const callbacks = {
  addResult: vi.fn(),
  resume: vi.fn(),
  respondToApproval: vi.fn(),
};

describe('FinancialSourcesToolUI', () => {
  it('renders every returned financial source instead of hiding after twelve', () => {
    const items = Array.from({ length: 47 }, (_, index) => ({
      route_path: `/source/${index + 1}`,
      name: `来源 ${index + 1}`,
      namespace: `ns-${Math.floor(index / 5)}`,
      namespace_name: `来源组 ${Math.floor(index / 5)}`,
      description: `第 ${index + 1} 个来源`,
      capabilities: ['market'],
    }));

    render(
      <FinancialSourcesToolUI
        {...callbacks}
        type="tool-call"
        toolCallId="sources-all"
        toolName="list_financial_sources"
        args={{}}
        argsText="{}"
        result={{ success: true, catalog_count: 47, matched_count: 47, item_count: 47, returned_count: 47, items }}
        status={completed}
      />,
    );

    expect(screen.getByText('资讯源目录 47 个 · 匹配 47 个 · 返回 47 个')).toBeInTheDocument();
    expect(screen.getByText('/source/1')).toBeInTheDocument();
    expect(screen.getByText('/source/47')).toBeInTheDocument();
    expect(screen.queryByText(/已展示前 12 个/)).not.toBeInTheDocument();
  });

  it('explains that an empty keyword result is not a news-content search', () => {
    render(
      <FinancialSourcesToolUI
        {...callbacks}
        type="tool-call"
        toolCallId="sources-ai"
        toolName="list_financial_sources"
        args={{ keyword: 'AI' }}
        argsText="{}"
        result={{
          success: true,
          catalog_count: 47,
          matched_count: 0,
          returned_count: 0,
          items: [],
          query_note: 'keyword 仅筛选来源目录；搜索资讯内容请使用 search_financial_news。',
          applied_filters: { keyword: 'AI', capability: 'all' },
        }}
        status={completed}
      />,
    );

    expect(screen.getByText('资讯源目录 47 个 · 匹配 0 个 · 返回 0 个')).toBeInTheDocument();
    expect(screen.getByText(/如需查找“AI”相关资讯/)).toBeInTheDocument();
  });

  it('renders concrete dynamic source choices and their ids', () => {
    render(
      <FinancialSourcesToolUI
        {...callbacks}
        type="tool-call"
        toolCallId="source-inspect"
        toolName="inspect_financial_source"
        args={{ route_path: '/cls/subject/:id?' }}
        argsText="{}"
        result={{
          success: true,
          route: {
            route_path: '/cls/subject/:id?',
            name: '财联社话题',
            params: [{ name: 'id', required: false, hint: '话题编号' }],
          },
          dynamic_options: {
            subjects: [{ subjectId: 1279, name: '半导体芯片' }],
          },
        }}
        status={completed}
      />,
    );

    expect(screen.getByText('动态选项 · 1 个')).toBeInTheDocument();
    expect(screen.getByText('半导体芯片')).toBeInTheDocument();
    expect(screen.getByText('1279')).toBeInTheDocument();
  });
});
