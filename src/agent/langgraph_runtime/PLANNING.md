# Planning 协调器运行契约

本功能沿用 `create_agent`、ToolRegistry、AtomicToolExecutor、LangGraph checkpoint、证据检查及 Reflection。五种产品模式共享这些底层运行时边界；Planning 是 Plan 模式中的协调中间件，不是第二套执行器，也不提供独立的 replan 开关。Goal 是独立的顶层执行模式。

## 入口

产品层明确提供五种模式：`Auto`、`Direct`、`Plan`、`Team`、`Goal`。聊天选择器发送 `agent_mode=auto|direct|plan|team|goal`，模式在本轮开始前确定并在执行期间保持不变：Direct 走现有 Agent Loop，Plan 走本 PlanningCoordinator，Team 走独立的 supervisor/worker 协作图，Goal 走独立的 GoalGraph；Auto 交给 Runtime 层模型在当前已开放的执行路径之间路由，并且是默认模式。

`agent_mode=plan` 进入 PlanningCoordinator；`agent_mode=team` 直接进入 Team 图；`agent_mode=goal` 直接进入独立 GoalGraph，不调用 Plan/Team 业务协议；`agent_mode=auto` 先在 runtime 的 `_dispatch_product_mode` 顶层步骤中调用 Auto 路由模型，选择 Direct、Plan、Team，或在灰度开关开启后选择 Goal，才进入被选中的图，不能先进入 `build_team_graph` 再决定。Direct、Plan、Team 继续共享标准 checkpoint；Goal 使用 `goal-v1:{conversation_id}` 独立 checkpoint，但共享证据、审批、恢复和最终发布边界。

Goal 默认只允许用户显式选择；部署验证稳定后，设置服务端环境变量
`AGENT_GOAL_AUTO_ROUTING_ENABLED=true` 才开放 Auto -> Goal。显式
`agent_mode=goal` 不受该开关影响。

Auto 模式默认由模型选择 Direct、Plan 或 Team；Goal 只有在 `AGENT_GOAL_AUTO_ROUTING_ENABLED=true` 灰度开关打开后才加入 Auto 候选。Direct 模式使用原有问答循环，不生成 Planning 步骤；Plan 模式显式生成步骤并逐步核验；Team 模式显式生成领域任务并并行交接；Goal 模式围绕 GoalContract、证据门禁和服务端预算执行独立动作循环。内部直接调用 runtime 的默认值仍是 direct，测试或受控调用方可以明确传 `agent_mode`。

## 执行与完成

1. 计划必须有目标、初始状态、约束、完成条件和依赖步骤。重复编号、未知依赖、环、未知工具及超预算计划会被拒绝，不通过扩大工具权限来修复。
2. 代码只选择依赖已满足的步骤；工具绑定和实际执行策略共享同一允许列表。副作用仍需现有审批。
3. 数据步骤可多轮取证。每轮观察之后，模型返回 `PlanningStepReport`，评估是否继续当前步骤、完成、重规划或停止。工具成功不等于步骤完成。
4. 完成条件使用服务端提供的数字编号；条件正文由服务端映射。报告引用复用 `evidence_source_catalog` / `resolve_answer_sources` 的数字 `source_ids`，不能让模型抄写哈希编号。报告须逐项评估，并引用实际存在且可用的本轮证据。语义充分性由模型评估，结构、引用和实际执行情况由代码检查；这不等于对模型语义判断作绝对正确保证。
5. 纯分析步骤也实际调用模型，保存具体分析产出，不能以占位状态直接跳过。
6. 最后一步必须逐项检查原始总体目标。已执行所有步骤但目标仍有缺口时，不能声称目标完成。
7. 最终结构化回答还需通过已有证据与 Reflection 检查。最终答案未通过时，Planning 不能显示整体完成。

## 重规划与预算

重规划保留完成步骤、原始目标和约束，只替换剩余步骤。新旧步骤、原因、版本及失败报告进入同一个持久化记录。总步骤数最多 8；默认最多 2 次重规划，硬上限 4；同一步最多 3 轮 worker 调用。每个结构化合约最多修复一次；自动路由与计划是两个独立合约，整体模型和工具预算继续由现有运行时控制。

路由、规划、Team worker、冲突检测或报告校验耗尽时，明确返回部分结果/缺口，不回退到无计划的任意工具执行。

## Team 协作控制面

Team 是 PDF 第 7 篇的 Custom Hybrid Collaboration，不复用 Plan 的
`PlanningCoordinator`、计划步骤执行器或 Team→Plan 回接。每次 Team 运行拥有独立的
`CollaborationPlan`：`CollaborationCoordinator/Supervisor →
CollaborationPlanValidator → TeamDispatch → Send(注册表中选中的专家) →
AgentHandoffCommit → WorkerFailurePolicy → EvidenceMerger → DraftAggregator →
ReviewDispatch → ConflictDetector + CriticReviewer → ReviewGate`。

`ExpertRegistry` 只描述可用专家，不代表本轮一定执行。Supervisor 只能从注册表选择
至少两个有意义的专家任务；服务端校验专家身份、能力、只读工具范围、依赖环、并发、预算、
超时和成功条件。`TeamDispatch` 使用 LangGraph 原生 `Send` 只分发当前依赖已满足的任务，
每个注册专家挂载独立 graph node/namespace；执行函数可以共享，但任务身份、上下文、工具、
输出 Schema、checkpoint namespace 和事件流必须独立。新增注册专家不会被隐式执行。

`AgentHandoffCommit` 将每个结果保存为 `AgentReport` 并登记到服务端
Canonical Evidence Catalog；后续 Draft、Reviewer、Consensus 和 FinalSynthesizer 只能引用
canonical evidence ID。`WorkerFailurePolicy` 是 worker 层的唯一失败决策点：`retry` 只重试
当前失败任务，`replan` 进入 Team 自己的 `ReexecutionPlanner`，`partial` 保留证据并继续，
`abort` 终止 Team。已成功且证据有效的专家在同一运行中不可重复执行。

`ConflictDetector` 和 `CriticReviewer` 在 ReviewDispatch 后并行执行，`ReviewGate` 等待两者
都完成后再决定是否进入完成条件检查；只有冲突/高风险才通过 `Send` 并行执行
`BullCaseReviewer`、`BearCaseReviewer`，随后进入 `ConsensusResolver`。
无冲突也必须经过 `CompletionCriteriaValidator`。完成条件未通过时，流程进入
`ReexecutionPlanner → 目标专家 → EvidenceMerger → DraftAggregator → Review`；达到预算仍
无法修复则输出明确的 `partial` / `blocked`，不会静默降级为 Direct。

每个节点都有独立 Pydantic 交接契约。worker/reviewer 子图使用 per-invocation 模式
（`checkpointer=None`），继承根 Team 图的数据库 checkpoint；根图保存任务、尝试、证据交接、
审查、共识和再执行决定。结构化合约、专家任务和工具调用均有独立 deadline，取消、超时、
模型失败和工具失败都写入终态事件，父 Team 不得永久保持 `running`。

代码中的 `completion_criteria_validator`、`team_synthesizer` 是唯一规范节点名；Team
不再注册或读取其他历史节点名。

## 展示与上下文

- 计划摘要和步骤进度是模型产生的安全简述，经核验后进入原生正文流；不是从工具数量、目标字段拼装的固定文案。
- 聊天沿用正常 Markdown 正文与工具折叠组。完整计划、逐项条件检查、证据及重规划记录可在运行详情查看。
- 过程文本不是最终答案。受阻结果在最终发布前确定 partial 状态，避免先发布一次成功答案再重复追加整段部分答案。
- 完整历史留在 checkpoint。Planner 解析追问语境，worker 只接收当前目标、初始状态、核验报告及当前步骤的工具消息对，不回放上一轮的结构化答案协议。
- 历史文字不是本轮已核验的证据；跨实体取数、统一口径和综合比较不能仅因已知代码/工具名称就被判断为单个直接查询。
- 最终答案的语义核验失败时，请求投影将原先表示“结构解析成功”的 ToolMessage 改成实际核验错误回执，沿用 LangChain 的工具错误修复对话；不能同时告诉模型“已成功”和“请修订”。原始 checkpoint 不被改写。
- 规划内部结构化调用的原始流不会混入用户正文；只发布正式的模型摘要。

## 定向验证

模型适配层遵循 LangChain 的结构化输出解析器，并在 Planning 合约上显式指定对应工具名、关闭并行工具调用、使用非流式请求；`include_raw=True` 保留安全的原始响应元信息，使“没有工具调用”和“工具调用参数校验失败”可以区分。LiteLLM 的静态能力表只作为参考，真正以网关返回的兼容工具调用为准。`any` / `True` 转为 `required`，指定工具名转为 function 对象，`auto` 保持自动选择。结构化输出不能依赖提示词强制；普通执行步骤也不能因此失去自然正文。

Planner 目录只传递操作名、简短描述、effect 和 category；完整参数 schema 仍由 LangChain 在执行步骤时绑定给 worker。这样保留工具选择权威边界，同时避免规划请求被 66 个工具的全部字段和来源描述挤占。

普通问答的请求上下文同样会将上一轮结构化答案渲染为普通对话内容，不回放它的输出协议回执。本轮原生工具消息对保持不变，完整 checkpoint 与运行记录不受此投影影响。

后端重点见 `tests/test_agent_planning.py` 和 `tests/test_langgraph_multi_agent_team.py`：四种产品模式、Auto 路由、非法计划、条件未满足、伪造证据、重规划、实际分析、上下文投影、审批恢复、并行 worker、冲突/多空/共识和部分答案发布。另运行受影响的原生流、checkpoint、审批及持久化测试。

前端重点验证 AgentReasoning、ChatRuntimeBridge、agentStage 和 RunDetailContent。真实验收还需在页面验证简单解释、短句多步骤任务、失败重规划及刷新回放，不能用单元测试代替模型和浏览器验收。
