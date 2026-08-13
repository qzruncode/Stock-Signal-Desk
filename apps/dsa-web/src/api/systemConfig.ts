import apiClient from './index';
import { createParsedApiError, getParsedApiError, type ParsedApiError } from './error';
import { toCamelCase } from './utils';
import type {
  SetupStatusResponse,
  SystemConfigConflictResponse,
  SystemConfigResponse,
  SystemConfigSchemaResponse,
  SystemConfigValidationErrorResponse,
  TestNotificationChannelRequest,
  TestNotificationChannelResponse,
  TestModelConnectionRequest,
  TestModelConnectionResponse,
  UpdateSystemConfigRequest,
  UpdateSystemConfigResponse,
} from '../types/systemConfig';

export class SystemConfigValidationError extends Error {
  issues: SystemConfigValidationErrorResponse['issues'];
  parsedError: ParsedApiError;

  constructor(message: string, issues: SystemConfigValidationErrorResponse['issues'], parsedError?: ParsedApiError) {
    super(message);
    this.name = 'SystemConfigValidationError';
    this.issues = issues;
    this.parsedError = parsedError ?? createParsedApiError({
      title: '配置校验失败',
      message,
      rawMessage: message,
      status: 400,
      category: 'http_error',
    });
  }
}

export class SystemConfigConflictError extends Error {
  currentConfigVersion?: string;
  parsedError: ParsedApiError;

  constructor(message: string, currentConfigVersion?: string, parsedError?: ParsedApiError) {
    super(message);
    this.name = 'SystemConfigConflictError';
    this.currentConfigVersion = currentConfigVersion;
    this.parsedError = parsedError ?? createParsedApiError({
      title: '配置版本冲突',
      message,
      rawMessage: message,
      status: 409,
      category: 'http_error',
    });
  }
}

function toSnakeUpdatePayload(payload: UpdateSystemConfigRequest): Record<string, unknown> {
  return {
    config_version: payload.configVersion,
    mask_token: payload.maskToken ?? '******',
    reload_now: payload.reloadNow ?? true,
    items: payload.items.map((item) => ({
      key: item.key,
      value: item.value,
    })),
  };
}

function toSnakeNotificationTestPayload(payload: TestNotificationChannelRequest): Record<string, unknown> {
  return {
    channel: payload.channel,
    items: (payload.items || []).map((item) => ({
      key: item.key,
      value: item.value,
    })),
    mask_token: payload.maskToken ?? '******',
    title: payload.title ?? 'Stock Assistant 通知测试',
    content: payload.content ?? '这是一条来自 Stock Assistant Web 设置页的通知测试消息。',
    timeout_seconds: payload.timeoutSeconds ?? 20,
  };
}

export const systemConfigApi = {
  async getConfig(includeSchema = true, revealSensitive = false): Promise<SystemConfigResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/system/config', {
      params: { include_schema: includeSchema, reveal_sensitive: revealSensitive },
    });
    return toCamelCase<SystemConfigResponse>(response.data);
  },

  async getSchema(): Promise<SystemConfigSchemaResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/system/config/schema');
    return toCamelCase<SystemConfigSchemaResponse>(response.data);
  },

  async getSetupStatus(): Promise<SetupStatusResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/system/config/setup/status');
    return toCamelCase<SetupStatusResponse>(response.data);
  },

  async testNotificationChannel(payload: TestNotificationChannelRequest): Promise<TestNotificationChannelResponse> {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/system/config/notification/test-channel',
      toSnakeNotificationTestPayload(payload),
    );
    return toCamelCase<TestNotificationChannelResponse>(response.data);
  },

  async testModelConnection(payload: TestModelConnectionRequest): Promise<TestModelConnectionResponse> {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/system/config/model/test-connection',
      {
        items: (payload.items || []).map((item) => ({
          key: item.key,
          value: item.value,
        })),
        mask_token: payload.maskToken ?? '******',
        timeout_seconds: payload.timeoutSeconds ?? 30,
      },
    );
    return toCamelCase<TestModelConnectionResponse>(response.data);
  },

  async update(payload: UpdateSystemConfigRequest): Promise<UpdateSystemConfigResponse> {
    try {
      const response = await apiClient.put<Record<string, unknown>>(
        '/api/v1/system/config',
        toSnakeUpdatePayload(payload),
      );
      return toCamelCase<UpdateSystemConfigResponse>(response.data);
    } catch (error: unknown) {
      const parsed = getParsedApiError(error);
      if (error && typeof error === 'object' && 'response' in error) {
        const status = (error as { response?: { status?: number } }).response?.status;
        const payloadData = (error as { response?: { data?: unknown } }).response?.data;

        if (status === 400) {
          const validationError = toCamelCase<SystemConfigValidationErrorResponse>(payloadData ?? {});
          throw new SystemConfigValidationError(
            parsed.message || validationError.message || '配置校验失败',
            validationError.issues || [],
            parsed,
          );
        }

        if (status === 409) {
          const conflict = toCamelCase<SystemConfigConflictResponse>(payloadData ?? {});
          throw new SystemConfigConflictError(
            parsed.message || conflict.message || '配置版本冲突',
            conflict.currentConfigVersion,
            parsed,
          );
        }
      }

      throw error;
    }
  },
};
