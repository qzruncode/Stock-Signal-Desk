type SystemConfigCategory =
  | 'base'
  | 'data_source'
  | 'ai_model'
  | 'notification'
  | 'system'
  | 'agent'
  | 'uncategorized';

type SystemConfigDataType =
  | 'string'
  | 'integer'
  | 'number'
  | 'boolean'
  | 'array'
  | 'json'
  | 'time';

type SystemConfigUIControl =
  | 'text'
  | 'password'
  | 'number'
  | 'select'
  | 'textarea'
  | 'switch'
  | 'time';

export interface SystemConfigOption {
  label: string;
  value: string;
}

interface SystemConfigDocLink {
  label: string;
  href: string;
}

export interface SystemConfigFieldSchema {
  key: string;
  title?: string;
  description?: string;
  category: SystemConfigCategory;
  dataType: SystemConfigDataType;
  uiControl: SystemConfigUIControl;
  isSensitive: boolean;
  isRequired: boolean;
  isEditable: boolean;
  defaultValue?: string | null;
  options: Array<string | SystemConfigOption>;
  validation: Record<string, unknown>;
  displayOrder: number;
  helpKey?: string | null;
  examples?: string[];
  docs?: SystemConfigDocLink[];
  warningCodes?: string[];
}

export interface SystemConfigCategorySchema {
  category: SystemConfigCategory;
  title: string;
  description?: string;
  displayOrder: number;
  fields: SystemConfigFieldSchema[];
}

export interface SystemConfigSchemaResponse {
  schemaVersion: string;
  categories: SystemConfigCategorySchema[];
}

interface SystemConfigItem {
  key: string;
  value: string;
  rawValueExists: boolean;
  isMasked: boolean;
  schema?: SystemConfigFieldSchema;
}

export interface SystemConfigResponse {
  configVersion: string;
  maskToken: string;
  items: SystemConfigItem[];
  updatedAt?: string;
}

export interface TestModelConnectionRequest {
  items?: SystemConfigUpdateItem[];
  maskToken?: string;
}

export interface TestModelConnectionResponse {
  success: boolean;
  message: string;
  errorCode?: string | null;
  stage?: string | null;
  retryable: boolean;
  latencyMs?: number | null;
}

interface SetupStatusCheck {
  key: string;
  title: string;
  category: 'base' | 'ai_model' | 'agent' | 'notification' | 'system';
  required: boolean;
  status: 'configured' | 'inherited' | 'optional' | 'needs_action';
  message: string;
  nextStep?: string | null;
}

export interface SetupStatusResponse {
  isComplete: boolean;
  readyForSmoke: boolean;
  requiredMissingKeys: string[];
  nextStepKey?: string | null;
  checks: SetupStatusCheck[];
}

interface SystemConfigUpdateItem {
  key: string;
  value: string;
}

export interface UpdateSystemConfigRequest {
  configVersion: string;
  maskToken?: string;
  reloadNow?: boolean;
  items: SystemConfigUpdateItem[];
}

export interface UpdateSystemConfigResponse {
  success: boolean;
  configVersion: string;
  appliedCount: number;
  skippedMaskedCount: number;
  reloadTriggered: boolean;
  updatedKeys: string[];
  warnings: string[];
}

interface ConfigValidationIssue {
  key: string;
  code: string;
  message: string;
  severity: 'error' | 'warning';
  expected?: string;
  actual?: string;
}

type NotificationTestChannel = 'wechat';

interface NotificationTestAttempt {
  channel: NotificationTestChannel;
  success: boolean;
  message: string;
  target?: string | null;
  errorCode?: string | null;
  stage: string;
  retryable: boolean;
  latencyMs?: number | null;
  httpStatus?: number | null;
}

export interface TestNotificationChannelRequest {
  channel: NotificationTestChannel;
  items?: SystemConfigUpdateItem[];
  maskToken?: string;
  title?: string;
  content?: string;
  timeoutSeconds?: number;
}

export interface TestNotificationChannelResponse {
  success: boolean;
  message: string;
  errorCode?: string | null;
  stage?: string | null;
  retryable: boolean;
  latencyMs?: number | null;
  attempts: NotificationTestAttempt[];
}

export interface SystemConfigValidationErrorResponse {
  error: string;
  message: string;
  issues: ConfigValidationIssue[];
}

export interface SystemConfigConflictResponse {
  error: string;
  message: string;
  currentConfigVersion: string;
}
