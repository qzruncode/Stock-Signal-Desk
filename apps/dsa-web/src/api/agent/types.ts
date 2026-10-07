export interface ChatConversationItem {
  id: string;
  title: string;
  titleSource: 'auto' | 'manual' | string;
  previewText?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ChatConversationMessage {
  id: string;
  conversationId: string;
  role: 'user' | 'assistant' | 'system' | string;
  content: string;
  sequence: number;
  createdAt: string;
}

interface ChatConversationThreadStateMessage {
  message: Record<string, unknown>;
  parentId: string | null;
  runConfig?: Record<string, unknown>;
}

interface ChatConversationThreadState {
  headId?: string | null;
  messages: ChatConversationThreadStateMessage[];
}

interface PersistedAgentStage {
  event: 'agent_stage' | 'agent_stage_v2';
  runId?: string;
  run_id?: string;
  stage: string;
  status: 'started' | 'completed' | 'succeeded' | 'failed' | 'blocked' | 'cancelled';
  taskId?: string | null;
  task_id?: string | null;
  actionId?: string | null;
  action_id?: string | null;
  toolCallId?: string | null;
  tool_call_id?: string | null;
  roundId?: string | null;
  round_id?: string | null;
  errorCode?: string | null;
  error_code?: string | null;
  summary?: string;
  occurredAt?: string;
  occurred_at?: string;
  details?: Record<string, unknown>;
}

type AgentAnswerBlockPresentation =
  | 'markdown'
  | 'table'
  | 'code'
  | 'json'
  | 'list'
  | 'quote'
  | string;

interface StructuredAnswerBlockProjection {
  section: string;
  kind: string;
  presentationType: AgentAnswerBlockPresentation;
  language: string;
  content: string;
  evidenceIds: string[];
  artifactRefs?: StructuredAnswerArtifactReference[];
  chartRefs?: StructuredAnswerChartReference[];
  actionRefs?: StructuredAnswerActionReference[];
}

export interface StructuredAnswerArtifactReference {
  artifactId: string;
  artifactType: 'file' | 'document' | string;
  title: string;
  mimeType: string;
  downloadUrl: string;
  previewUrl?: string | null;
  actionId?: string | null;
  evidenceId?: string | null;
  toolName?: string | null;
}

interface StructuredAnswerChartSeries {
  key: string;
  label: string;
}

export interface StructuredAnswerChartReference {
  chartId: string;
  chartType: 'line' | 'bar' | 'area' | string;
  title: string;
  xKey: string;
  series: StructuredAnswerChartSeries[];
  data: Array<Record<string, string | number>>;
  actionId?: string | null;
  evidenceId?: string | null;
}

export interface StructuredAnswerActionReference {
  actionId: string;
  toolName?: string | null;
  effect: 'read' | 'side_effect' | string;
  status: 'completed' | 'failed' | string;
  success: boolean;
  reused: boolean;
  evidenceId?: string | null;
}

export interface StructuredAnswerProjection {
  profile: 'general' | 'research' | string;
  title: string;
  blocks: StructuredAnswerBlockProjection[];
}

export interface AgentPlanningTrace {
  enabled: boolean;
  mode: string;
  status: string;
  revision: number;
  replanCount: number;
  replanLimit: number;
  modelCallCount: number;
  currentStepId?: string | null;
  error?: string | null;
  decision?: { mode: string; reason: string } | null;
  plan?: Record<string, unknown> | null;
  stepReports?: Record<string, unknown>[];
  updates?: PersistedAgentStage[];
}

interface AgentTeamResult {
  taskId?: string;
  agentId?: string;
  agentNode?: string;
  role?: string;
  status?: string;
  /** Server-owned retry attempt for this logical task. */
  attempt?: number;
  summary?: string;
  findings?: string[];
  findingEvidenceIds?: string[][];
  limitations?: string[];
  openQuestions?: string[];
  confidence?: string;
  failureStrategy?: string;
  evidenceIds?: string[];
  toolCallCount?: number;
  modelTurnCount?: number;
  assessmentStatus?: string;
  criteriaStatus?: string;
  criteriaChecks?: Record<string, unknown>[];
  unmetCriteria?: string[];
  errorCode?: string | null;
  errorDetail?: string | null;
  [key: string]: unknown;
}

interface AgentTeamWorkerHandoff {
  status?: string;
  taskIds?: string[];
  incompleteTaskIds?: string[];
  error?: string | null;
}

interface AgentTeamFailurePolicy {
  action?: string;
  taskIds?: string[];
  status?: string;
  error?: string | null;
}

interface AgentTeamFailure {
  status?: string;
  errorCode?: string | null;
  detail?: string | null;
  phase?: string | null;
  dispatchStatus?: string | null;
}

export interface AgentTeamTrace {
  agentMode?: string;
  resolvedAgentMode?: string;
  mode: string;
  requestedMode?: string;
  route?: string;
  executionStrategy?: string;
  routeReason?: string;
  teamId?: string;
  status?: string;
  workerCount?: number;
  completedWorkerCount?: number;
  planSource?: string;
  planError?: string | null;
  contractCallCount?: number;
  plan?: Record<string, unknown> | null;
  results?: AgentTeamResult[];
  review?: Record<string, unknown> | null;
  reviewStatus?: string;
  reviewError?: string | null;
  evidenceMerge?: Record<string, unknown> | null;
  evidenceMergeStatus?: string;
  conflict?: Record<string, unknown> | null;
  conflictStatus?: string;
  critic?: Record<string, unknown> | null;
  criticStatus?: string;
  criteriaAssessment?: Record<string, unknown> | null;
  criteriaStatus?: string;
  criteriaError?: string | null;
  bullCase?: Record<string, unknown> | null;
  bullCaseStatus?: string;
  bullCaseError?: string | null;
  bearCase?: Record<string, unknown> | null;
  bearCaseStatus?: string;
  bearCaseError?: string | null;
  consensus?: Record<string, unknown> | null;
  consensusStatus?: string;
  dispatchedTaskIds?: string[];
  dispatchRound?: number;
  taskAttempts?: Record<string, number>;
  workerHandoff?: AgentTeamWorkerHandoff | null;
  failurePolicy?: AgentTeamFailurePolicy | null;
  planningHandoff?: Record<string, unknown> | null;
  planningHandoffStatus?: string;
  planningHandoffError?: string | null;
  failure?: AgentTeamFailure | null;
}

interface AgentGoalCriterion {
  criterionId: string;
  description: string;
  required: boolean;
  verificationMethod: string;
  status: 'satisfied' | 'pending' | 'failed' | 'unverifiable' | string;
  evidenceIds: string[];
  explanation?: string;
}

interface AgentGoalAction {
  actionId: string;
  kind: 'tool' | 'ask_user' | 'finish' | string;
  toolName?: string | null;
  status?: string;
  criterionIds: string[];
  rationale?: string;
}

export interface AgentGoalTrace {
  schemaVersion?: string;
  status: string;
  objective: string;
  scope?: string;
  revision?: number;
  criteria: AgentGoalCriterion[];
  currentAction?: AgentGoalAction | null;
  lastAction?: AgentGoalAction | null;
  progress?: string;
  iteration?: number;
  replanCount?: number;
  limits?: {
    iterations?: number;
    replans?: number;
    toolCalls?: number;
    modelCalls?: number;
    actionValidationRepairs?: number;
    actionValidationRepairLimit?: number;
  };
  evidenceIds?: string[];
  blocker?: string | null;
  terminalReason?: string | null;
  confirmationStatus?: string;
  pendingConfirmationCriteria?: string[];
}

export interface AgentExecutionTrace {
  /** Product mode selected for this turn: auto, direct, plan, team, or goal. */
  agentMode?: string;
  /** Effective route selected by Auto, when Auto was requested. */
  resolvedAgentMode?: string | null;
  /** Ordered, bounded assistant-stream parts used for terminal replay. */
  displayPartsVersion?: number;
  displayParts?: Record<string, unknown>[];
  stages?: PersistedAgentStage[];
  actions?: Record<string, unknown>[];
  toolResults?: Record<string, unknown>[];
  evidence?: Record<string, unknown>[];
  claimEvidence?: Record<string, unknown>[];
  structuredAnswer?: StructuredAnswerProjection | null;
  planning?: AgentPlanningTrace | null;
  team?: AgentTeamTrace | null;
  goal?: AgentGoalTrace | null;
  loop?: Record<string, unknown>;
  completedToolCallIds?: string[];
  /** Defensive marker when a malformed API response was compacted for rendering. */
  clientTraceTruncated?: boolean;
}

/** One bounded execution projection belonging to one durable conversation turn. */
export interface AgentExecutionTraceRecord {
  runId: string;
  status?: string | null;
  errorCode?: string | null;
  finalText?: string | null;
  latestStage?: PersistedAgentStage | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  executionTrace?: AgentExecutionTrace | null;
}

interface AgentCheckpointTaskSummary {
  id?: string | null;
  name?: string | null;
  interruptCount: number;
  hasError: boolean;
  errorType?: string | null;
}

interface AgentCheckpointMetadata {
  source?: string | null;
  step?: number | null;
  writeKeys: string[];
}

interface AgentCheckpointStateSummary {
  agentMode?: string;
  resolvedAgentMode?: string | null;
  runId?: string | null;
  conversationId?: string | null;
  status?: string | null;
  messageCount: number;
  toolResultCount: number;
  evidenceCount: number;
  modelTurnCount: number;
  toolCallCount: number;
  evidenceRepairCount: number;
  planningEnabled?: boolean;
  planningMode?: string;
  planningStatus?: string;
  planningRevision?: number;
  planningCurrentStepId?: string | null;
  planningReplanCount?: number;
  planningStepCount?: number;
  goalStatus?: string;
  goalRevision?: number;
  goalIteration?: number;
  goalReplanCount?: number;
  goalCriterionCount?: number;
  goalEvidenceCount?: number;
  goalBlocker?: string | null;
  hasPendingInterrupt: boolean;
  hasAnswer: boolean;
}

/** Bounded, read-only projection of a native LangGraph checkpoint. */
export interface AgentCheckpointSummary {
  checkpointId?: string | null;
  parentCheckpointId?: string | null;
  createdAt?: string | null;
  metadata: AgentCheckpointMetadata;
  next: string[];
  tasks: AgentCheckpointTaskSummary[];
  state: AgentCheckpointStateSummary;
}

export interface AgentCheckpointHistoryResponse {
  conversationId: string;
  threadId: string;
  currentCheckpointId?: string | null;
  checkpointAuthority: string;
  runLifecycleAuthority: string;
  readOnly: boolean;
  items: AgentCheckpointSummary[];
  hasMore: boolean;
  nextBeforeCheckpointId?: string | null;
}

export interface PendingAgentInterrupt {
  interruptId: string;
  runId: string;
  conversationId?: string;
  fingerprint: string;
  actionId?: string;
  kind?: string;
  toolName?: string;
  summary: string;
  arguments?: Record<string, unknown>;
  goalContract?: Record<string, unknown> | null;
  criteria?: Record<string, unknown>[];
  pendingCriteria?: string[];
  action?: Record<string, unknown> | null;
  createdAt?: string;
}

export interface AgentInterruptDecision {
  decision: 'approve' | 'reject' | 'modify';
  message?: string;
}

export interface ChatConversationDetail extends ChatConversationItem {
  messages: ChatConversationMessage[];
  threadState?: ChatConversationThreadState | null;
  executionTrace?: AgentExecutionTrace | null;
  /** Per-run trace projections used to restore process disclosures in history. */
  executionTraces?: AgentExecutionTraceRecord[] | null;
  /** 后端是否仍在生成该对话的回复(刷新后前端据此判断是否续流)。 */
  isGenerating?: boolean;
  resumeState?: {
    runId?: string | null;
    active: boolean;
    isGenerating?: boolean;
    status?: 'running' | 'completed' | 'partial' | 'failed' | 'cancelled' | string | null;
    afterChunkIndex: number;
    eventCursor?: number;
    assistantText: string;
    hasToolEvents?: boolean;
    latestStage?: PersistedAgentStage | null;
    pendingInterrupt?: PendingAgentInterrupt | null;
  };
  pendingInterrupt?: PendingAgentInterrupt | null;
}

// Conversation detail is a rendering boundary, not an audit export. Project
// the current execution trace before camelcase-keys walks the payload so one
// oversized tool result cannot freeze the chat page during hydration.
// Bound each typed projection independently. A large transcript must not
// consume the evidence catalog's budget (turning valid citations into false
// "unresolved" warnings), or erase Team status. Field/array/depth caps still
// bound the whole response; raw audit payloads never enter the page state.

export interface ChatConversationListResponse {
  items: ChatConversationItem[];
  total: number;
  page: number;
  limit: number;
}
