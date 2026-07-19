import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import WorkflowToolsUI from './WorkflowToolsUI';

const callbacks = { addResult: vi.fn(), resume: vi.fn(), respondToApproval: vi.fn() };

describe('WorkflowToolsUI', () => {
  it('renders a persisted analysis task card', () => {
    render(
      <WorkflowToolsUI
        {...callbacks}
        type="tool-call"
        toolCallId="analysis-1"
        toolName="run_stock_analysis"
        args={{ symbol: '新强联' }}
        argsText="{}"
        result={{ success: true, accepted: true, task_id: 'task-1', stock_code: '002015', status: 'pending', message: '任务已加入队列' }}
        status={{ type: 'complete' }}
      />,
    );
    expect(screen.getByText('任务已加入队列')).toBeInTheDocument();
    expect(screen.getByText('任务 task-1')).toBeInTheDocument();
  });

  it('shows notification delivery result', () => {
    render(
      <WorkflowToolsUI
        {...callbacks}
        type="tool-call"
        toolCallId="notice-1"
        toolName="send_notification"
        args={{ content_type: 'custom', confirmed: true }}
        argsText="{}"
        result={{ success: true, sent: true, channel: 'wechat', message: '通知已发送' }}
        status={{ type: 'complete' }}
      />,
    );
    expect(screen.getByText('已发送至 wechat')).toBeInTheDocument();
  });

  it('renders custom watchlist group operations as first-class workflow cards', () => {
    render(
      <WorkflowToolsUI
        {...callbacks}
        type="tool-call"
        toolCallId="watchlist-1"
        toolName="manage_watchlist_groups"
        args={{ action: 'create', group: '核心观察' }}
        argsText="{}"
        result={{ success: true, action: 'create', message: '已创建自选分组「核心观察」' }}
        status={{ type: 'complete' }}
      />,
    );
    expect(screen.getByText('已创建自选分组「核心观察」')).toBeInTheDocument();
  });
});
