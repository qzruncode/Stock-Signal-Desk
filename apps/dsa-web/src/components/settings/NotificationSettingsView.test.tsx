import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { systemConfigApi } from '../../api/systemConfig';
import type {
  SystemConfigFieldSchema,
  SystemConfigResponse,
  SystemConfigSchemaResponse,
} from '../../types/systemConfig';
import { NotificationSettingsView } from './NotificationSettingsView';

vi.mock('../../api/systemConfig', () => ({
  SystemConfigConflictError: class SystemConfigConflictError extends Error {},
  SystemConfigValidationError: class SystemConfigValidationError extends Error {},
  systemConfigApi: {
    getSchema: vi.fn(),
    getConfig: vi.fn(),
    update: vi.fn(),
    testNotificationChannel: vi.fn(),
  },
}));

function notificationField(
  key: string,
  title: string,
): SystemConfigFieldSchema {
  return {
    key,
    title,
    description: `${title} description`,
    category: 'notification',
    dataType: 'string',
    uiControl: 'password',
    isSensitive: true,
    isRequired: false,
    isEditable: true,
    defaultValue: null,
    options: [],
    validation: {},
    displayOrder: 10,
  };
}

const wechatField = notificationField('WECHAT_WEBHOOK_URL', '企业微信 Webhook URL');
const feishuField = notificationField('FEISHU_WEBHOOK_URL', '飞书 Webhook URL');

const schema: SystemConfigSchemaResponse = {
  schemaVersion: 'test',
  categories: [{
    category: 'notification',
    title: '通知设置',
    displayOrder: 30,
    fields: [wechatField, feishuField],
  }],
};

const config: SystemConfigResponse = {
  configVersion: 'v1',
  maskToken: '******',
  items: [
    {
      key: 'WECHAT_WEBHOOK_URL',
      value: '',
      rawValueExists: false,
      isMasked: false,
      schema: wechatField,
    },
    {
      key: 'FEISHU_WEBHOOK_URL',
      value: '',
      rawValueExists: false,
      isMasked: false,
      schema: feishuField,
    },
  ],
};

describe('NotificationSettingsView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(systemConfigApi.getSchema).mockResolvedValue(schema);
    vi.mocked(systemConfigApi.getConfig).mockResolvedValue(config);
    vi.mocked(systemConfigApi.update).mockResolvedValue({
      success: true,
      configVersion: 'v2',
      appliedCount: 1,
      skippedMaskedCount: 0,
      reloadTriggered: true,
      updatedKeys: ['WECHAT_WEBHOOK_URL'],
      warnings: [],
    });
    vi.mocked(systemConfigApi.testNotificationChannel).mockResolvedValue({
      success: true,
      message: 'wechat 通知测试成功',
      retryable: false,
      attempts: [],
    });
  });

  it('only displays the enterprise WeChat webhook URL', async () => {
    render(<NotificationSettingsView />);

    expect(await screen.findByRole('heading', { name: '企业微信通知' })).toBeInTheDocument();
    expect(screen.getByLabelText('企业微信 Webhook URL')).toBeInTheDocument();
    expect(screen.queryByLabelText('飞书 Webhook URL')).not.toBeInTheDocument();
    expect(screen.queryByText('高级设置')).not.toBeInTheDocument();
  });

  it('only saves the enterprise WeChat webhook URL', async () => {
    render(<NotificationSettingsView />);
    const input = await screen.findByLabelText('企业微信 Webhook URL');
    const webhookUrl = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=new-key';

    fireEvent.change(input, { target: { value: webhookUrl } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(systemConfigApi.update).toHaveBeenCalledWith(expect.objectContaining({
        items: [{ key: 'WECHAT_WEBHOOK_URL', value: webhookUrl }],
      }));
    });
  });

  it('only tests the entered enterprise WeChat webhook URL', async () => {
    render(<NotificationSettingsView />);
    const input = await screen.findByLabelText('企业微信 Webhook URL');
    const webhookUrl = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=new-key';

    fireEvent.change(input, { target: { value: webhookUrl } });
    fireEvent.click(screen.getByRole('button', { name: '测试通知' }));

    await waitFor(() => {
      expect(systemConfigApi.testNotificationChannel).toHaveBeenCalledWith(expect.objectContaining({
        channel: 'wechat',
        items: [{ key: 'WECHAT_WEBHOOK_URL', value: webhookUrl }],
      }));
    });
  });
});
