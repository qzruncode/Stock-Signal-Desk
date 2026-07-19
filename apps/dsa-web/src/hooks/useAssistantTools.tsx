/**
 * 工具 UI 注册。
 *
 * assistant-ui 的 MessagePrimitive.Parts tools 配置有两种形态:
 *   - { by_name, Fallback }: 按 toolName 路由到指定组件,未命中走 Fallback
 *   - { Override }: 单一组件接管全部工具调用(忽略 by_name 注册表)
 *
 * 本项目用 by_name 形态:K线/行情/财务/新闻等高价值工具
 * 走内联可视化,其余 ~20 个工具走 GenericToolDrawer 抽屉看 JSON。
 *
 * by_name 直接传组件即可,无需 makeAssistantToolUI 注册(后者依赖在
 * Provider 内渲染注册组件,且与 Override 互斥)。
 */

export { default as KlineToolUI } from '../components/assistant-ui/tool-ui/KlineToolUI';
export { default as RealtimeQuotesToolUI } from '../components/assistant-ui/tool-ui/RealtimeQuotesToolUI';
export { default as FinancialsToolUI } from '../components/assistant-ui/tool-ui/FinancialsToolUI';
export { default as NewsToolUI } from '../components/assistant-ui/tool-ui/NewsToolUI';
export { default as RssFeedToolUI } from '../components/assistant-ui/tool-ui/RssFeedToolUI';
export {
  FinancialArticleToolUI,
  FinancialExportToolUI,
  FinancialFeedToolUI,
  FinancialSourcesToolUI,
} from '../components/assistant-ui/tool-ui/FinancialNewsToolsUI';
export { default as GenericToolUI } from '../components/assistant-ui/tool-ui/GenericToolDrawer';
export { default as WorkflowToolsUI } from '../components/assistant-ui/tool-ui/WorkflowToolsUI';
