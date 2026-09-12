import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StructuredAnswerReferences } from './StructuredAnswerReferences';
import {
  stripStructuredAnswerReferenceFallbacks,
  structuredAnswerFromTrace,
} from './StructuredAnswerReferencesUtils';
import type { StructuredAnswerProjection } from '../../api/agent';

const answer: StructuredAnswerProjection = {
  profile: 'research',
  title: '筛选结果',
  blocks: [{
    section: '',
    kind: 'fact',
    presentationType: 'markdown',
    language: '',
    content: '结果',
    evidenceIds: [],
    artifactRefs: [{
      artifactId: 'stock-screen-20260912-120000-abcdef12.csv',
      artifactType: 'file',
      title: '筛选结果.csv',
      mimeType: 'text/csv',
      downloadUrl: '/api/v1/agent/exports/stock-screen-20260912-120000-abcdef12.csv',
    }],
    actionRefs: [{
      actionId: 'screen-1',
      toolName: 'screen_atr_volatility_stocks',
      effect: 'read',
      status: 'completed',
      success: true,
      reused: false,
    }],
  }],
};

describe('StructuredAnswerReferences', () => {
  it('renders safe file downloads and read-only action summaries', () => {
    render(<StructuredAnswerReferences answer={answer} />);

    expect(screen.getByText('筛选结果.csv')).toBeInTheDocument();
    expect(screen.getByText(/screen_atr_volatility_stocks/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /下载/ })).toHaveAttribute(
      'href',
      '/api/v1/agent/exports/stock-screen-20260912-120000-abcdef12.csv',
    );
  });

  it('reads either trace key without accepting a missing structured answer', () => {
    expect(structuredAnswerFromTrace({ structured_answer: answer })).toBe(answer);
    expect(structuredAnswerFromTrace({ structuredAnswer: { blocks: [] } })).toBeNull();
    expect(structuredAnswerFromTrace(null)).toBeNull();
  });

  it('removes server fallback reference lines when typed cards are available', () => {
    const rendered = [
      '结果正文',
      '',
      '- [下载文件：筛选结果.csv](/api/v1/agent/exports/stock-screen-20260912-120000-abcdef12.csv)',
      '- 动作记录：screen_atr_volatility_stocks（已完成，仅展示，不会再次执行）',
    ].join('\n');

    expect(stripStructuredAnswerReferenceFallbacks(rendered, answer)).toBe('结果正文');
  });

  it('removes chart fallback lines even when an older trace has a machine title', () => {
    const chartAnswer: StructuredAnswerProjection = {
      ...answer,
      blocks: [{
        ...answer.blocks[0]!,
        chartRefs: [{
          chartId: 'chart-1',
          chartType: 'line',
          title: 'read_stock_capital_flow_history_eastmoney数据',
          xKey: 'x',
          series: [{ key: 'main_net_inflow', label: '主力净流入' }],
          data: [{ x: '2026-09-11', main_net_inflow: 1 }],
        }],
      }],
    };

    expect(stripStructuredAnswerReferenceFallbacks([
      '结果正文',
      '',
      '- 图表：read_stock_capital_flow_history_eastmoney数据（已根据本轮工具数据生成）',
    ].join('\n'), chartAnswer)).toBe('结果正文');
  });
});
