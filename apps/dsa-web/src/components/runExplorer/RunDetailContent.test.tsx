import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AgentRunDetail } from '../../api/runExplorer';
import { RunDetailContent } from './RunDetailContent';
import { PlanningAuditCard } from './RunDetailCards';

it('shows persisted Planning reports and criterion checks independently of overall run success', () => {
  render(<PlanningAuditCard planning={{
    enabled: true, mode: 'planned', status: 'partial', revision: 2, replanCount: 1, replanLimit: 2,
    modelCallCount: 4, decision: { mode: 'planned', reason: '需要跨来源比较' },
    plan: { goal: '比较两家公司', steps: [{ stepId: 'first', objective: '核实口径', status: 'completed', completionCriteria: ['口径一致'] }] },
    stepReports: [{ stepId: 'first', status: 'completed', completedSummary: '两家公司均采用单季度口径。',
      evidenceIds: ['ev_real'], criteriaChecks: [{ criterion: '口径一致', satisfied: true, explanation: '已核对报告期' }] }],
  }} />);
  expect(screen.getByText(/未完整完成/)).toBeInTheDocument();
  expect(screen.getByText('需要跨来源比较')).toBeInTheDocument();
  fireEvent.click(screen.getByText(/1. 核实口径/));
  expect(screen.getByText('两家公司均采用单季度口径。')).toBeInTheDocument();
  expect(screen.getByText('通过：口径一致 — 已核对报告期')).toBeInTheDocument();
  expect(screen.getByText('关联证据：ev_real')).toBeInTheDocument();
});

it('keeps replaced-step reports and unmet overall criteria visible after replanning', () => {
  render(<PlanningAuditCard planning={{
    enabled: true, mode: 'planned', status: 'blocked', revision: 2, replanCount: 1, replanLimit: 2, modelCallCount: 5,
    plan: { steps: [{ stepId: 'replacement', objective: '替代来源', status: 'blocked' }] },
    stepReports: [{ stepId: 'original', status: 'blocked', planRevision: 1, completedSummary: '原来源只返回了部分数据。',
      goalChecks: [{ criterion: '两家公司口径一致', satisfied: false, explanation: '另一家公司仍缺少报告期' }] }],
  }} />);
  fireEvent.click(screen.getByText('最近一次总体目标核验'));
  expect(screen.getByText('未满足：两家公司口径一致 — 另一家公司仍缺少报告期')).toBeInTheDocument();
  fireEvent.click(screen.getByText('被替换步骤的历史报告'));
  expect(screen.getByText('原来源只返回了部分数据。')).toBeInTheDocument();
});

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

const completedWithFailedCall: AgentRunDetail = {
  ...detail,
  snapshot: {
    ...detail.snapshot,
    run: { status: 'completed', finalText: '回答已完成' },
    qualityProjection: {
      toolResults: [{
        actionId: 'rss-invalid', toolCallId: 'rss-ledger', toolName: 'read_rss_source',
        success: false, errorCode: 'invalid_arguments', errors: ['source_params.subject 不支持'],
      }],
      evidence: [],
    },
    steps: [
      { stepId: 'rss-invalid', toolCallId: 'rss-ledger', toolName: 'read_rss_source', status: 'failed', errorCode: 'invalid_arguments', errorDetail: 'source_params.subject 不支持' },
      { stepId: 'other-read', toolName: 'read_web_source', status: 'completed', errorDetail: '已通过备用来源返回' },
    ],
    behaviorAudit: {
      status: 'danger', attentionLevel: 'urgent', riskScore: 35,
      issueCount: 1, dangerCount: 1, warningCount: 0, infoCount: 0,
      actionRequiredCount: 1, advisoryCount: 0,
      modelTurnCount: 2, toolCallCount: 2, toolObservationCount: 2,
      contentReadCallCount: 1, contentExtractedCallCount: 1,
      referenceOnlyToolCount: 0, referenceLinkCount: 0,
      unreadReferenceCount: 0, unreadDocumentCount: 0, unreadArticleCount: 0,
      failedToolCount: 1, evidenceCount: 1, claimCount: 1,
      checks: [], toolChain: [],
      findings: [{
        code: 'tool_execution_failed', severity: 'danger', category: 'execution',
        disposition: 'action_required', title: '工具 read_rss_source 执行失败',
        detail: '工具调用记录失败：source_params.subject 不支持。',
        remediation: '检查来源参数。', actionIds: ['rss-invalid'],
      }],
    },
  },
  score: { ...detail.score, status: 'passed', passed: true, totalScore: 1 },
};

describe('RunDetailContent', () => {
  it('keeps a completed run distinct from a failed tool attempt and deduplicates the ledger', () => {
    render(<RunDetailContent detail={completedWithFailedCall} onFeedback={vi.fn()} />);

    expect(screen.getByText('已完成，有 1 次工具调用失败')).toBeInTheDocument();
    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(screen.queryByText('查看运行错误详情')).not.toBeInTheDocument();
    expect(screen.queryByText('本次运行失败')).not.toBeInTheDocument();
    expect(screen.getByText('查看工具调用错误')).toBeInTheDocument();
    expect(screen.getByText('工具调用存在未解决的问题')).toBeInTheDocument();
    expect(screen.getByText('100%')).toBeInTheDocument();
    expect(screen.getByText(/不代表事实准确率/)).toBeInTheDocument();
    expect(screen.queryByText(/自动检查通过/)).not.toBeInTheDocument();
    expect(screen.queryByText(/自动核对未发现待处理异常/)).not.toBeInTheDocument();
  });

  it('retains recovered failures as history without reviving an actionable issue', () => {
    const audit = completedWithFailedCall.snapshot.behaviorAudit!;
    const recovered: AgentRunDetail = {
      ...completedWithFailedCall,
      snapshot: {
        ...completedWithFailedCall.snapshot,
        behaviorAudit: {
          ...audit, status: 'info', attentionLevel: 'none', riskScore: 0,
          dangerCount: 0, infoCount: 1, actionRequiredCount: 0, advisoryCount: 1,
          findings: [{
            ...audit.findings[0], severity: 'info', disposition: 'advisory',
            title: '工具 read_rss_source 曾失败但已恢复',
          }],
        },
      },
    };
    render(<RunDetailContent detail={recovered} onFeedback={vi.fn()} />);

    expect(screen.getByText('已完成，有 1 次工具调用失败')).toBeInTheDocument();
    expect(screen.getByText('工具 read_rss_source 曾失败但已恢复')).toBeInTheDocument();
    expect(screen.queryByText('工具调用存在未解决的问题')).not.toBeInTheDocument();
    expect(screen.queryByText('查看运行错误详情')).not.toBeInTheDocument();
  });

  it('shows failures from historical ledger-only records without changing run status', () => {
    render(<RunDetailContent detail={{
      ...completedWithFailedCall,
      snapshot: { ...completedWithFailedCall.snapshot, qualityProjection: { toolResults: [], evidence: [] } },
    }} onFeedback={vi.fn()} />);

    expect(screen.getByText('已完成，有 1 次工具调用失败')).toBeInTheDocument();
    expect(screen.getByText('查看工具调用错误')).toBeInTheDocument();
    expect(screen.queryByText('查看运行错误详情')).not.toBeInTheDocument();
  });

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
    expect(screen.queryByText('工具调用存在未解决的问题')).not.toBeInTheDocument();
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
    expect(screen.getAllByText(/错误代码：tool_execution_failed/).length).toBeGreaterThan(0);
    expect(screen.getByText('工具调用存在未解决的问题')).toBeInTheDocument();
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

    expect(screen.getByText('执行诊断')).toBeInTheDocument();
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
