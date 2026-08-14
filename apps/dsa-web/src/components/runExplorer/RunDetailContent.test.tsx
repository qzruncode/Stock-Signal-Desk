import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AgentRunDetail } from '../../api/runExplorer';
import { RunDetailContent } from './RunDetailContent';

const detail: AgentRunDetail = {
  snapshot: {
    run: {
      status: 'partial',
      errorCode: 'agent_runtime_failed',
      errorDetail: 'ConnectionError: upstream closed the connection',
      finalText: '',
      createdAt: '2026-08-14T10:00:00+08:00',
    },
    trace: {},
    qualityProjection: {
      toolResults: [{
        actionId: 'call-kline',
        toolName: 'read_recent_kline',
        success: false,
        errorCode: 'tool_execution_failed',
        errors: ["ConnectionError: ('Connection aborted.', RemoteDisconnected('upstream closed'))"],
        result: {
          errors: ['The upstream service did not return a response'],
        },
      }],
      evidence: [],
    },
    steps: [],
    artifacts: [],
    feedback: null,
  },
  score: {
    evaluatorVersion: 'test',
    status: 'failed',
    passed: false,
    totalScore: 0,
    minimumScore: 0.85,
    dimensions: {},
    violations: [],
  },
};

describe('RunDetailContent', () => {
  it('shows expandable run and tool error details', () => {
    render(<RunDetailContent detail={detail} onFeedback={vi.fn()} />);

    expect(screen.getByText('查看运行错误详情')).toBeInTheDocument();
    expect(screen.getByText('查看错误详情')).toBeInTheDocument();
    expect(screen.getByText(/ConnectionError: upstream closed the connection/)).toBeInTheDocument();
    expect(screen.getAllByText(/The upstream service did not return a response/).length).toBeGreaterThan(0);
    expect(screen.getByText(/错误代码：tool_execution_failed/)).toBeInTheDocument();
    expect(screen.getByText('工具执行没有全部完成')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '定位失败工具' })).toBeInTheDocument();
    expect(screen.getByText('查看错误详情').closest('details')).toBeInTheDocument();
  });

  it('loads on demand and displays the actual tool request and response', () => {
    const onLoadToolPayloads = vi.fn();
    render(
      <RunDetailContent
        detail={detail}
        onFeedback={vi.fn()}
        onLoadToolPayloads={onLoadToolPayloads}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '加载请求与返回' }));
    expect(onLoadToolPayloads).toHaveBeenCalledTimes(1);
  });

  it('renders source metadata and sample rows from a loaded tool payload', () => {
    const loadedDetail: AgentRunDetail = {
      ...detail,
      snapshot: {
        ...detail.snapshot,
        qualityProjection: {
          ...detail.snapshot.qualityProjection,
          toolResults: [{
            actionId: 'call-kline',
            toolName: 'read_recent_kline',
            success: true,
            arguments: { sourceId: 'eastmoney', symbol: '000682', count: 3 },
          }],
        },
        steps: [{
          stepId: 'call-kline',
          toolName: 'read_recent_kline',
          arguments: { sourceId: 'eastmoney', symbol: '000682', count: 3 },
          result: {
            success: true,
            source: '腾讯财经日线（AKShare）',
            sourceKey: 'tencent',
            sourceOrigin: 'tencent',
            count: 3,
            dataTime: '2026-08-13',
            fallbackUsed: true,
            fallbackProvider: 'tencent',
            sourceAttempts: [{ source: 'eastmoney', error: 'RemoteDisconnected' }, { source: 'tencent', status: 'success', count: 3 }],
            data: [{ date: '2026-08-11', close: 1 }, { date: '2026-08-13', close: 2 }],
          },
        }],
      },
    };

    render(<RunDetailContent detail={loadedDetail} onFeedback={vi.fn()} toolPayloadsLoaded />);

    fireEvent.click(screen.getByRole('button', { name: '查看请求与返回' }));
    expect(screen.getByText('腾讯财经日线（AKShare）')).toBeInTheDocument();
    expect(screen.getByText('eastmoney')).toBeInTheDocument();
    expect(screen.getByText(/2026\/08\/13/)).toBeInTheDocument();
    expect(screen.getByText(/"symbol": "000682"/)).toBeInTheDocument();
    expect(screen.getAllByText(/"close": 2/).length).toBeGreaterThan(0);
  });
});
