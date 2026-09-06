import { beforeEach, describe, expect, it, vi } from 'vitest';
import { systemConfigApi } from '../systemConfig';

const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());

vi.mock('../index', () => ({
  default: {
    get,
    post,
    put: vi.fn(),
  },
}));

describe('systemConfigApi', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it('sends notification channel test payloads with snake_case fields', async () => {
    post.mockResolvedValueOnce({
      data: {
        success: true,
        message: 'ok',
        error_code: null,
        stage: 'notification_send',
        retryable: false,
        latency_ms: 15,
        attempts: [
          {
            channel: 'custom',
            success: true,
            message: 'sent',
            target: 'https://example.com/hook?token=***',
            error_code: null,
            stage: 'notification_send',
            retryable: false,
            latency_ms: 15,
            http_status: 200,
          },
        ],
      },
    });

    const result = await systemConfigApi.testNotificationChannel({
      channel: 'custom',
      items: [{ key: 'CUSTOM_WEBHOOK_URLS', value: 'https://example.com/hook?token=secret' }],
      maskToken: '******',
      title: 'hello',
      content: 'world',
      timeoutSeconds: 7,
    });

    expect(post).toHaveBeenCalledWith(
      '/api/v1/system/config/notification/test-channel',
      {
        channel: 'custom',
        items: [{ key: 'CUSTOM_WEBHOOK_URLS', value: 'https://example.com/hook?token=secret' }],
        mask_token: '******',
        title: 'hello',
        content: 'world',
        timeout_seconds: 7,
      },
    );
    expect(result.latencyMs).toBe(15);
    expect(result.attempts[0].errorCode).toBeNull();
    expect(result.attempts[0].httpStatus).toBe(200);
  });

  it('loads first-run setup status with camelCase fields', async () => {
    get.mockResolvedValueOnce({
      data: {
        is_complete: false,
        ready_for_smoke: false,
        required_missing_keys: ['llm_primary'],
        next_step_key: 'llm_primary',
        checks: [
          {
            key: 'llm_primary',
            title: 'LLM 主渠道',
            category: 'ai_model',
            required: true,
            status: 'needs_action',
            message: '缺少主模型配置',
            next_step: '打开系统设置',
          },
        ],
      },
    });

    const result = await systemConfigApi.getSetupStatus();

    expect(get).toHaveBeenCalledWith('/api/v1/system/config/setup/status');
    expect(result.isComplete).toBe(false);
    expect(result.nextStepKey).toBe('llm_primary');
    expect(result.checks[0].nextStep).toBe('打开系统设置');
  });

  it('sends model connectivity tests with the current form values', async () => {
    post.mockResolvedValueOnce({
      data: {
        success: true,
        message: '模型连接成功',
        error_code: null,
        stage: 'model_response',
        retryable: false,
        latency_ms: 21,
      },
    });

    const result = await systemConfigApi.testModelConnection({
      items: [
        { key: 'ANTHROPIC_BASE_URL', value: 'https://gw.example.com' },
        { key: 'ANTHROPIC_AUTH_TOKEN', value: 'secret-token' },
        { key: 'ANTHROPIC_MODEL', value: 'configured/provider-model' },
      ],
      maskToken: '******',
      timeoutSeconds: 9,
    });

    expect(post).toHaveBeenCalledWith(
      '/api/v1/system/config/model/test-connection',
      {
        items: [
          { key: 'ANTHROPIC_BASE_URL', value: 'https://gw.example.com' },
          { key: 'ANTHROPIC_AUTH_TOKEN', value: 'secret-token' },
          { key: 'ANTHROPIC_MODEL', value: 'configured/provider-model' },
        ],
        mask_token: '******',
        timeout_seconds: 9,
      },
    );
    expect(result.latencyMs).toBe(21);
  });
});
