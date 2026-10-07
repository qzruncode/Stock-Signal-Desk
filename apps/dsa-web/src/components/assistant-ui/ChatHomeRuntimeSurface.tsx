import type React from 'react';
import { AssistantRuntimeProvider } from '@assistant-ui/react';
import { ChatRuntimeBridge, type ChatRuntimeBridgeProps } from './ChatRuntimeBridge';
import { ChatLayout, type ChatLayoutProps } from './ChatLayout';

type ChatHomeRuntimeSurfaceProps = {
  runtime: React.ComponentProps<typeof AssistantRuntimeProvider>['runtime'];
  bridgeProps: ChatRuntimeBridgeProps;
  layoutProps: ChatLayoutProps;
};

/** Keep the page focused on orchestration while this component owns the runtime surface. */
export const ChatHomeRuntimeSurface: React.FC<ChatHomeRuntimeSurfaceProps> = ({
  runtime,
  bridgeProps,
  layoutProps,
}) => (
  <AssistantRuntimeProvider runtime={runtime}>
    <ChatRuntimeBridge {...bridgeProps} />
    <ChatLayout {...layoutProps} />
  </AssistantRuntimeProvider>
);
