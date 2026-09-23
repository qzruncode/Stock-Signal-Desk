import { useMemo } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AssistantRuntimeProvider, MessagePrimitive, ThreadPrimitive, useExternalStoreRuntime } from '@assistant-ui/react';
import type { ThreadMessageLike } from '@assistant-ui/react';
import { GoalMessageContent } from './GoalMessageContent';

const AssistantMessage = () => <MessagePrimitive.Root><GoalMessageContent /></MessagePrimitive.Root>;
const Harness = ({ message }: { message: ThreadMessageLike }) => {
  const messages = useMemo(() => [message], [message]);
  const runtime = useExternalStoreRuntime({ messages, convertMessage: (item) => item, isRunning: message.status?.type === 'running', onNew: async () => {} });
  return <AssistantRuntimeProvider runtime={runtime}><ThreadPrimitive.Messages components={{ Message: AssistantMessage }} /></AssistantRuntimeProvider>;
};

const projection = (text: string, id = 'goal:1', sequence = 1) => ({
  type: 'data' as const, name: 'agent-model-projection',
  data: { text, scope: 'goal', projection_source: 'model', projection_id: id, sequence },
});
const message = (content: ThreadMessageLike['content'], running = false): ThreadMessageLike => ({
  id: 'goal-message', role: 'assistant', content,
  status: running ? { type: 'running' } : { type: 'complete', reason: 'stop' },
});

describe('Goal conversation projection', () => {
  it('keeps growing model prose visible without waiting for punctuation or balanced brackets', () => {
    const view = render(<Harness message={message([projection('我先确认这份记录（')], true)} />);
    expect(screen.getByText('我先确认这份记录（')).toBeInTheDocument();
    view.rerender(<Harness message={message([projection('我先确认这份记录（含发布时间），再核对来源', 'goal:1', 2)], true)} />);
    expect(screen.getByText('我先确认这份记录（含发布时间），再核对来源')).toBeInTheDocument();
    expect(view.container.querySelectorAll('[data-agent-display-part="model-projection"]')).toHaveLength(1);
  });

  it('does not invent a narrative from internal stages while awaiting model output', () => {
    render(<Harness message={message([
      { type: 'data', name: 'agent-stage', data: { event: 'agent_stage', stage: 'goal.intake', status: 'started', summary: '正在提取目标、约束和完成条件' } },
      { type: 'reasoning', text: 'PRIVATE REASONING' },
    ], true)} />);
    expect(screen.queryByText('正在提取目标、约束和完成条件')).not.toBeInTheDocument();
    expect(screen.queryByText('PRIVATE REASONING')).not.toBeInTheDocument();
    expect(screen.getByRole('status', { name: '主 Agent 回复生成中' })).toBeInTheDocument();
  });

  it('anchors replayed updates at their first position instead of moving prose past a tool', () => {
    const view = render(<Harness message={message([
      projection('我先核对', 'same', 1),
      { type: 'tool-call', toolCallId: 'read-1', toolName: 'search_source', args: {}, result: { success: true } },
      projection('我先核对发布来源', 'same', 2),
    ])} />);
    expect(view.container.querySelectorAll('[data-agent-display-part="model-projection"]')).toHaveLength(1);
    const prose = screen.getByText('我先核对发布来源');
    const tool = screen.getByRole('button', { name: '展开工具 search_source 详情' });
    expect(prose.compareDocumentPosition(tool) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('replays prose, tool, observation and accepted answer in the original order', () => {
    const view = render(<Harness message={message([
      projection('我先核对公开记录。'),
      { type: 'tool-call', toolCallId: 'read-1', toolName: 'search_source', args: {}, result: { success: true, result: { value: 'record' } } },
      projection('找到了，发布时间也能对应上', 'goal:2'),
      { type: 'text', text: '核对结果：这条记录有效。', providerMetadata: { dsa: { displayKind: 'answer' } } },
    ])} />);
    const first = screen.getByText('我先核对公开记录。');
    const tool = screen.getByRole('button', { name: '展开工具 search_source 详情' });
    const observation = screen.getByText('找到了，发布时间也能对应上');
    const answer = screen.getByText('核对结果：这条记录有效。');
    expect(first.compareDocumentPosition(tool) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(tool.compareDocumentPosition(observation) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(observation.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(view.container.querySelector('[data-goal-board]')).toBeNull();
    expect(screen.queryByRole('status', { name: '主 Agent 回复生成中' })).not.toBeInTheDocument();
  });
});
