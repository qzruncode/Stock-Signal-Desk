---
name: batch-code-scan
description: Use when the user invokes /batch-code-scan or asks to scan the whole current project once, build a complete inventory of actionable code risks, split all discovered problems into independent draft Human Plans, and let different sessions continue each plan through the normal dev/check/approve/audit loop without repeated rescans.
---

# Batch Code Scan

所有产出使用简体中文。代码标识、路径、API 名称、错误信息和引用原文不翻译。

只允许扫描代码并写入 `docs/human-plans/` 下的批次索引和候选 Human Plan，禁止修改其他项目文件。输出后立即停止，不得自动进入 `/dev`。

## 目标

一次扫描当前项目，形成完整问题库存，再把所有可行动问题按独立修复范围拆成多个短 Human Plan。每个 Plan 后续由用户手动在一个会话中执行 `/dev <Plan Ref>`，再继续 `/plan-check`、`/design-check`、`approve`、`/audit`。需要并行处理多个 Plan 时，先执行 `/worktree <Plan Ref>`，再按其输出进入独立 worktree 继续 `/dev <Plan Ref>`。

## Plan 约束

- 一个候选修复范围一个 Plan 文件和 Plan ID；同一批次共享 Batch ID。
- 每个 Plan 只覆盖一个连贯修复范围，避免把无关问题塞进同一计划。
- 不只输出最高优先级问题；当前可访问代码中发现的所有可行动问题都必须进入批次索引。
- Plan 面向人类审核，只写问题价值、目标结果、边界、依赖和验收结果，不写代码、逐文件改动或 AI 执行步骤。
- 固定字段为：Plan ID、Version、Owner Skill、Status、Frontend Impact、Requirement Baseline、Confirmed Decisions、Current Plan、Changes Since Last Plan、Unchanged Scope、Needs Reconfirmation、Review Status、Delivery Status、Revision Notes。
- 每个候选 Human Plan 都是人类与 AI 的需求对齐凭据，不是扫描报告或技术修复计划。
- 固定字段必须保留；不适用字段写 `无`，不要为了填字段展开解释。
- 每个候选 Plan 只回答：为什么修、修完行为会怎样、明确不做什么、怎么验收。
- 每个候选 Plan 的 Current Plan 就是最终需求提示词：loop 多轮后，人类和 AI 已达成一致，AI 可按它执行。
- 候选 Plan 禁止写文件路径、函数名、行号、内部实现步骤、测试命令或技术排查过程。
- AI 后续执行时可自行推导技术步骤；Human Plan 只保存双方对齐后的需求契约。
- Changes Since Last Plan 只写本轮需求变化一句话，不保留多轮历史。
- Needs Reconfirmation 只写当前未解决的人类决策问题，不写分析过程或完整 replan 草稿。
- Review Status、Delivery Status 和 Revision Notes 只写短状态，不写检查报告。
- 批次索引保存完整问题库存，但每项只保留人类能理解的问题标题、影响、级别和归属 Plan Ref，不展开分析过程。
- 聊天输出只展示批次摘要、Plan Ref 列表和下一步命令，不重复每个 Plan 全文。
- Owner Skill 为 `batch-code-scan`；Status 只使用 `draft` 或 `reconfirmation-pending`；Frontend Impact 只使用 `yes`、`no` 或 `unknown`。
- Plan Ref 固定为 `<Plan 文件路径>@v<Version>`。引用版本不一致时停止且不写入，并返回当前 Plan Ref。
- 任一命令的 Owner、Status 或前置条件不满足时停止且不写入，并根据当前 Plan 返回合法下一步。

## 批次索引

`/batch-code-scan` 必须额外写一个批次索引，索引用于分发，不进入 `/dev`。索引是后续工作的唯一库存，必须能避免用户为了发现剩余问题而重复调用 `/batch-code-scan`。索引只保留：

- Batch ID、扫描范围和扫描时间。
- 扫描覆盖面：已覆盖的主要目录、入口、接口、数据流、配置和前端页面；明确跳过项及原因。
- 问题库存：每个问题的短标题、证据位置、严重级别、影响面、归属 Plan Ref。
- 每个候选 Plan 的标题、包含的问题、严重级别、影响面、Plan Ref、是否适合并行。
- 计划之间的依赖、冲突和建议执行顺序。
- 下一步命令示例：单会话处理使用 `/dev <Plan Ref>`；并行处理使用 `/worktree <Plan Ref>`；需要澄清时先用 `/batch-code-scan replan <Plan Ref>`。

## Reconfirmation

Needs Reconfirmation 非空时，`replan` 只能准备待提交 Replan，不能直接更新正式 Plan：

- 使用当前消息中的人类答复，不从更早对话猜测。
- 在 Needs Reconfirmation 中用短句保留人类问题、AI 理解和拟变更点。
- 不清除确认项，不修改正式 Plan，不增加 Version。
- Status 设为 `reconfirmation-pending`，只展示待提交 Replan 的理解摘要和拟变更点后停止。

当前消息没有可用于对应确认项的答复时，不写入任何内容，只展示待确认事项并要求用户在 `/batch-code-scan replan <当前 Plan Ref>` 后补充答复。

理解不正确时继续 `/batch-code-scan replan <当前 Plan Ref>` 修正待提交 Replan。只有精确的 `/batch-code-scan confirm <当前 Plan Ref>` 才能提交；自然语言肯定不算 confirm。未经 confirm，不得进入 `/dev`。

## `/batch-code-scan`

扫描当前项目的核心代码、配置、接口、数据流、前端状态、复用边界、性能、安全和可维护性风险。

执行时先建立项目覆盖面，再形成完整问题库存，最后拆成候选 Plan：

- 不得在发现一两个严重问题后停止；必须继续覆盖其他主要模块。
- 不得用“后续再扫描”“下一轮再补充”替代本次库存。
- 高影响、高确定性问题优先排序，但低优先级的真实问题也要进入库存。
- 小问题可合并进同一修复范围；无共同边界的问题必须拆开。
- 能并行的计划保持需求边界清晰。
- 会互相冲突、共享同一抽象或依赖前置改造的计划在索引中标为串行。
- 证据不足或需要人类判断的计划，把问题写入 Needs Reconfirmation。

创建批次索引和所有候选 Plan 后，只展示索引摘要、每个真实 Plan Ref、建议并行分组和下一步命令。并行分组里的每个 Plan 给出可直接复制的 `/worktree <Plan Ref>`。随后停止。

如果发现已有未完成批次索引，先读取并复用它；除非用户明确要求刷新，或代码已在批次扫描后发生明显变化，不要重新全量扫描。

## `/batch-code-scan replan [Plan Ref]`

要求 Owner Skill 为 `batch-code-scan`，Status 为 `draft` 或 `reconfirmation-pending`。

- Needs Reconfirmation 为空：按人类反馈调整该候选 Plan 的优先级、范围、依赖或边界，增加 Version，Status 保持 `draft`。
- Needs Reconfirmation 非空：按 Reconfirmation 协议准备或修正待提交 Replan，Version 不变。

只更新当前 Plan；如需调整批次索引，只同步该 Plan 的标题、状态、Plan Ref、依赖、问题归属或并行建议。不重新扫描全项目。展示短摘要、真实 Plan Ref 和合法下一步后停止。

## `/batch-code-scan confirm [Plan Ref]`

仅当当前消息精确为 `/batch-code-scan confirm <当前 Plan Ref>` 时执行。要求 Owner Skill 为 `batch-code-scan`、Status 为 `reconfirmation-pending`，并存在对应当前 Version 的待提交 Replan。

提交待提交 Replan，清除已解决的确认项，增加 Version 并记录变化。仍有未解决项时 Status 设为 `draft`，下一步继续 `/batch-code-scan replan <新 Plan Ref>`；全部解决后 Status 设为 `draft`，下一步进入 `/dev <新 Plan Ref>`。confirm 只提交 Replan，不代表开发批准。
