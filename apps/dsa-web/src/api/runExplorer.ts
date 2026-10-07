import apiClient from './index';
import { toCamelCase } from './utils';
import type { AgentGoalTrace, AgentPlanningTrace, AgentTeamTrace } from './agent';

type AgentRunStatus =
  | 'queued'
  | 'running'
  | 'recovering'
  | 'interrupted'
  | 'completed'
  | 'partial'
  | 'failed'
  | 'cancelled'
  | 'blocked';

export interface AgentRunFeedback {
  id: string;
  runId: string;
  rating: -1 | 1;
  category?: string | null;
  comment?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

type AgentBehaviorStatus = 'clear' | 'info' | 'warning' | 'danger';

interface AgentBehaviorFinding {
  code: string;
  severity: 'info' | 'warning' | 'danger';
  disposition?: 'action_required' | 'advisory' | string;
  category: string;
  title: string;
  detail: string;
  remediation?: string;
  confidence?: number;
  toolNames?: string[];
  actionIds?: string[];
  links?: string[];
}

interface AgentBehaviorCheck {
  code: string;
  label: string;
  status: AgentBehaviorStatus;
  detail: string;
}

interface AgentBehaviorStep {
  actionId?: string;
  toolName?: string;
  success?: boolean;
  behavior?: string;
  accessStatus?: string;
  referenceLinkCount?: number;
  contentExtracted?: boolean;
  evidenceCount?: number;
  resultCount?: number | null;
  outcome?: {
    executionStatus?: 'completed' | 'failed' | string;
    accessStatus?: string;
    dataStatus?: string;
    usable?: boolean;
    qualityStatus?: string;
  };
}

export interface AgentBehaviorAudit {
  schemaVersion?: string;
  status: AgentBehaviorStatus;
  attentionLevel: 'none' | 'review' | 'urgent';
  riskScore: number;
  issueCount: number;
  actionRequiredCount?: number;
  advisoryCount?: number;
  dangerCount: number;
  warningCount: number;
  infoCount?: number;
  modelTurnCount: number;
  toolCallCount: number;
  toolObservationCount: number;
  contentReadCallCount: number;
  contentExtractedCallCount: number;
  referenceOnlyToolCount: number;
  referenceLinkCount: number;
  unreadReferenceCount: number;
  unreadDocumentCount: number;
  unreadArticleCount: number;
  citedReferenceToolCount?: number;
  citedUnreadReferenceCount?: number;
  failedToolCount: number;
  goalActionFailureCount?: number;
  evidenceCount: number;
  claimCount: number;
  checks: AgentBehaviorCheck[];
  findings: AgentBehaviorFinding[];
  toolChain: AgentBehaviorStep[];
  sampling?: {
    mode: 'on_demand' | string;
    available: boolean;
    sampleLimit: number;
    targets: Array<{ url: string; kind?: string }>;
    note?: string;
  };
}

interface AgentSourceSampleResult {
  url: string;
  kind?: string | null;
  success: boolean;
  status: 'readable' | 'unreadable' | string;
  finalUrl?: string | null;
  contentType?: string | null;
  extractionMethod?: string | null;
  contentLength?: number;
  contentPreview?: string | null;
  errors?: string[];
  warnings?: string[];
}

export interface AgentSourceSampleResponse {
  runId: string;
  mode: string;
  sampledAt: string;
  sampleLimit: number;
  sampledCount: number;
  items: AgentSourceSampleResult[];
  note: string;
}

export interface AgentRunSummary {
  runId: string;
  conversationId: string;
  status: AgentRunStatus;
  errorCode?: string | null;
  engine?: string | null;
  tools: string[];
  toolObservationCount: number;
  evidenceCount: number;
  evidenceLinksVerified: boolean;
  qualityScore: number;
  qualityStatus: 'passed' | 'failed';
  behaviorStatus?: AgentBehaviorStatus;
  behaviorAttentionLevel?: 'none' | 'review' | 'urgent';
  behaviorIssueCount?: number;
  behaviorDangerCount?: number;
  behaviorWarningCount?: number;
  behaviorInfoCount?: number;
  behaviorRiskScore?: number;
  behaviorActionRequiredCount?: number;
  behaviorAdvisoryCount?: number;
  unreadReferenceCount?: number;
  unreadDocumentCount?: number;
  unreadArticleCount?: number;
  citedReferenceToolCount?: number;
  citedUnreadReferenceCount?: number;
  contentReadCallCount?: number;
  failedToolCount?: number;
  feedback?: AgentRunFeedback | null;
  toolCallCount: number;
  providerCallCount: number;
  estimatedTokenCount: number;
  estimatedCostMicros: number;
  durationMs?: number | null;
  createdAt: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  finalTextPreview?: string;
}

export interface AgentRunListResponse {
  items: AgentRunSummary[];
  total: number;
  page: number;
  limit: number;
}

interface AgentRunQualityDimension {
  score: number;
  weight: number;
  details: Record<string, unknown>;
}

interface AgentRunQualityScore {
  evaluatorVersion: string;
  status: 'passed' | 'failed';
  passed: boolean;
  totalScore: number;
  minimumScore: number;
  dimensions: Record<string, AgentRunQualityDimension>;
  violations: Array<{ code: string; details: unknown }>;
  feedback?: AgentRunFeedback | Record<string, never>;
}

export interface AgentRunDetail {
  snapshot: {
    run: Record<string, unknown>;
    trace: Record<string, unknown>;
    qualityProjection: {
      agentMode?: string;
      resolvedAgentMode?: string | null;
      planning?: AgentPlanningTrace;
      team?: AgentTeamTrace;
      goal?: AgentGoalTrace;
      inspectionSchemaVersion?: string;
      toolResults?: Array<Record<string, unknown>>;
      evidence?: Array<Record<string, unknown>>;
      claimEvidence?: Array<Record<string, unknown>>;
      runtimeErrors?: Array<Record<string, unknown>>;
      loop?: Record<string, unknown>;
      completedToolCallIds?: string[];
    };
    behaviorAudit?: AgentBehaviorAudit;
    steps: Array<Record<string, unknown>>;
    artifacts: Array<Record<string, unknown>>;
    feedback?: AgentRunFeedback | null;
  };
  score: AgentRunQualityScore;
}

export interface AgentQualitySummary {
  windowDays: number;
  terminalRuns: number;
  quality: {
    scoredRuns: number;
    passedRuns: number;
    passRate?: number | null;
    averageScore?: number | null;
    dimensionScores: Record<string, number>;
    violations: Record<string, number>;
  };
  behavior?: {
    auditedRuns: number;
    clearRuns: number;
    warningRuns: number;
    dangerRuns: number;
    infoRuns: number;
    actionRequiredCount: number;
    advisoryCount: number;
    unreadReferenceCount: number;
    contentReadCallCount: number;
    issues: Record<string, number>;
  };
  feedback: {
    total: number;
    positive: number;
    negative: number;
    positiveRate?: number | null;
  };
  releaseGate: {
    evaluations: number;
    passed: number;
    failed: number;
    passRate?: number | null;
  };
}

export const runExplorerApi = {
  async listRuns(params: {
    status?: string;
    tool?: string;
    page?: number;
    limit?: number;
  } = {}): Promise<AgentRunListResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/agent/runs', { params });
    return toCamelCase<AgentRunListResponse>(response.data);
  },

  async getRun(
    runId: string,
    options: { includePayloads?: boolean } = {},
  ): Promise<AgentRunDetail> {
    const response = await apiClient.get<Record<string, unknown>>(`/api/v1/agent/runs/${runId}`, {
      params: options.includePayloads ? { include_payloads: true } : undefined,
    });
    return toCamelCase<AgentRunDetail>(response.data);
  },

  async sampleSources(runId: string, limit = 3): Promise<AgentSourceSampleResponse> {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/agent/runs/${runId}/audit/sample`,
      undefined,
      { params: { limit } },
    );
    return toCamelCase<AgentSourceSampleResponse>(response.data);
  },

  async getQualitySummary(days = 30): Promise<AgentQualitySummary> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/agent/quality/summary', {
      params: { days },
    });
    return toCamelCase<AgentQualitySummary>(response.data);
  },

  async saveFeedback(
    runId: string,
    rating: -1 | 1,
    category?: string,
  ): Promise<AgentRunFeedback> {
    const response = await apiClient.put<Record<string, unknown>>(
      `/api/v1/agent/runs/${runId}/feedback`,
      { rating, category },
    );
    return toCamelCase<AgentRunFeedback>(response.data);
  },
};
