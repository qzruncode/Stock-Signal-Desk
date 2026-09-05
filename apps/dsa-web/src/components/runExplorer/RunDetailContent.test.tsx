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
  it('distinguishes evidence failure from failed tools and exempts disclaimers', () => {
    const evidenceDetail: AgentRunDetail = {
      ...detail,
      snapshot: {
        ...detail.snapshot,
        run: { ...detail.snapshot.run, errorCode: 'evidence_link_incomplete', errorDetail: '证据关联修订预算已用尽', finalText: '研究结果' },
        qualityProjection: {
          toolResults: [{ actionId: 'read', toolName: 'read_quote', success: true }],
          claimEvidence: [
            { claimId: 'risk', text: '风险结论', evidenceIds: ['ev_valid'], unresolvedEvidenceIds: ['ev_missing'], checks: { source: true, time: true } },
            { claimId: 'disclaimer', text: '仅供研究参考', requiresEvidence: false, evidenceIds: [], checks: { source: true, time: true } },
          ],
          evidence: [],
        },
      },
      score: { ...detail.score, dimensions: { execution: { score: 0.75, weight: 0.25, details: { status: 'partial' } } } },
    };
    render(<RunDetailContent detail={evidenceDetail} onFeedback={vi.fn()} />);
    expect(screen.queryByText('工具执行没有全部完成')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '定位失败工具' })).not.toBeInTheDocument();
    expect(screen.getByText('运行未完整完成')).toBeInTheDocument();
    expect(screen.getByText('无效引用：ev_missing')).toBeInTheDocument();
    expect(screen.getByText('无需引证')).toBeInTheDocument();
  });

  it('shows expandable run and tool error details', () => {
    render(<RunDetailContent detail={detail} onFeedback={vi.fn()} />);

    expect(screen.getByText('查看运行错误详情')).toBeInTheDocument();
    expect(screen.getByText('查看错误详情')).toBeInTheDocument();
    expect(screen.getByText(/ConnectionError: upstream closed the connection/)).toBeInTheDocument();
    expect(screen.getAllByText(/The upstream service did not return a response/).length).toBeGreaterThan(0);
    expect(screen.getByText(/错误代码：tool_execution_failed/)).toBeInTheDocument();
    expect(screen.getByText('工具执行没有全部完成')).toBeInTheDocument();
    expect(screen.queryByText('回答内容还有待补全')).not.toBeInTheDocument();
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

  it('prioritizes behavior findings and labels reference-only links', () => {
    const auditedDetail: AgentRunDetail = {
      ...detail,
      snapshot: {
        ...detail.snapshot,
        run: { status: 'completed', finalText: '结论' },
        qualityProjection: {
          toolResults: [{
            actionId: 'call-research',
            toolName: 'read_company_research_reports_akshare',
            success: true,
            referenceLinks: ['https://example.test/report.pdf'],
          }],
          evidence: [],
        },
        behaviorAudit: {
          status: 'warning',
          attentionLevel: 'review',
          riskScore: 12,
          issueCount: 1,
          dangerCount: 0,
          warningCount: 1,
          modelTurnCount: 2,
          toolCallCount: 1,
          toolObservationCount: 1,
          contentReadCallCount: 0,
          contentExtractedCallCount: 0,
          referenceOnlyToolCount: 1,
          referenceLinkCount: 1,
          unreadReferenceCount: 1,
          unreadDocumentCount: 1,
          unreadArticleCount: 0,
          failedToolCount: 0,
          evidenceCount: 0,
          claimCount: 0,
          checks: [{ code: 'content_access', label: '来源读取', status: 'warning', detail: '未读取正文' }],
          findings: [{
            code: 'reference_only_document',
            severity: 'warning',
            category: 'content_access',
            title: '发现文档来源，但没有对应正文读取',
            detail: '工具只返回 PDF 链接。',
            remediation: '继续读取正文。',
            links: ['https://example.test/report.pdf'],
          }],
          toolChain: [],
        },
      },
    };

    render(<RunDetailContent detail={auditedDetail} onFeedback={vi.fn()} />);

    expect(screen.getByText('自动巡检结论')).toBeInTheDocument();
    expect(screen.getByText('发现文档来源，但没有对应正文读取')).toBeInTheDocument();
    expect(screen.getByText('调用完成 · 来源索引')).toBeInTheDocument();
    expect(screen.getByText(/有 1 个需要处理的核对问题/)).toBeInTheDocument();
  });

  it('renders one normalized tool outcome instead of contradictory status badges', () => {
    const outcomeDetail: AgentRunDetail = {
      ...detail,
      snapshot: {
        ...detail.snapshot,
        run: { status: 'completed', finalText: '结论' },
        qualityProjection: {
          ...detail.snapshot.qualityProjection,
          toolResults: [{
            actionId: 'call-empty',
            toolName: 'read_web_source',
            success: true,
            outcome: {
              executionStatus: 'completed',
              accessStatus: 'content_unavailable',
              dataStatus: 'empty',
              usable: false,
              qualityStatus: 'warning',
            },
          }],
        },
      },
    };

    render(<RunDetailContent detail={outcomeDetail} onFeedback={vi.fn()} />);

    expect(screen.getByText('调用完成 · 空结果')).toBeInTheDocument();
    expect(screen.queryByText('成功')).not.toBeInTheDocument();
    expect(screen.queryByText('读取失败')).not.toBeInTheDocument();
  });
});
