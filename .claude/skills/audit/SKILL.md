---
name: audit
description: Use when the user invokes /audit or asks to review completed AI-written changes against the approved requirement, existing codebase, and engineering quality.
---

# Audit

所有产出使用简体中文。代码标识、路径、API 名称、错误信息和引用原文不翻译。

## 权限

- `/audit` 只允许审查代码并更新当前 Plan。
- `/audit replan` 和 `/audit confirm` 只允许读取项目并写入当前 Plan。
- 只有当前消息精确为 `/audit approve <当前 Plan Ref>` 时才允许修改源码。
- approve 前除当前 Plan 外禁止任何写入；审计发现问题后不得直接修复。

## Plan 约束

- 始终沿用原 Plan 文件和 Plan ID。
- Audit 可接收 Owner Skill 为 `dev`、`bug-fix` 或 `audit` 的已实现版本。
- 审计需要返工时把 Owner Skill 设为 `audit`。
- Audit 返工阶段的 Status 只使用 `audit-fixes-required`、`review-pending`、`replan-required`、`reconfirmation-pending`、`ready-for-approval`、`implemented` 或 `complete`。
- 审计返工 Plan 是人类与 AI 的返工需求对齐凭据，不是审计报告或实现说明。
- 固定字段必须保留；不适用字段写 `无`，不要为了填字段展开解释。
- 返工 Plan 只回答：实现结果哪里偏离已批准需求、返工后应是什么行为、明确不做什么、怎么验收。
- Current Plan 就是最终返工需求提示词：loop 多轮后，人类和 AI 已达成一致，AI 可按它执行。
- 禁止写文件路径、函数名、行号、内部实现步骤、测试命令或技术排查过程，除非这些本身就是用户要审核的需求。
- approve 后 AI 可以在内部拆解技术执行步骤；这些步骤不写回 Human Plan，除非出现新的需求决策。
- 只把阻塞问题和必要返工范围写入 Plan；非阻塞观察、长分析和排查过程不进入 Plan。
- Changes Since Last Plan 只写本轮需求变化一句话，不保留多轮历史。
- Needs Reconfirmation 只写当前未解决的人类决策问题，不写分析过程或完整 replan 草稿。
- Review Status、Delivery Status 和 Revision Notes 只写短状态，不写检查报告。
- 聊天输出只展示结论、真实 Plan Ref 和下一步命令，不重复完整 Plan。
- Plan Ref 固定为 `<Plan 文件路径>@v<Version>`。引用版本不一致时停止且不写入，并返回当前 Plan Ref。
- 任一命令的 Owner、Status 或前置条件不满足时停止且不写入，并根据当前 Plan 返回合法下一步。

## Reconfirmation

Needs Reconfirmation 非空时，`replan` 只能准备待提交 Replan，不能直接更新正式 Plan：

- 使用当前消息中的人类答复，不从更早对话猜测。
- 在 Needs Reconfirmation 中用短句保留人类问题、AI 理解和拟变更点。
- 不清除确认项，不修改正式 Plan，不增加 Version，不恢复检查状态。
- Status 设为 `reconfirmation-pending`，只展示待提交 Replan 的理解摘要和拟变更点后停止。

当前消息没有可用于对应确认项的答复时，不写入任何内容，只展示待确认事项并要求用户在 `/audit replan <当前 Plan Ref>` 后补充答复。

理解不正确时继续 `/audit replan <当前 Plan Ref>` 修正待提交 Replan。只有精确的 `/audit confirm <当前 Plan Ref>` 才能提交；自然语言肯定不算 confirm。未经 confirm，不得 check 或 approve。

## `/audit [Plan Ref]`

要求 Status 为 `implemented`，且 Delivery Status 存在当前 Version 的 approval 和 implementation 记录。否则停止并返回当前合法下一步。

检查批准方案是否真正实现，以及正确性、边界、兼容性、回归、架构融入、抽象与重复、性能、交互、后端契约、数据一致性、安全、重试、超时、降级、可用性和必要验证。

- 通过且 Needs Reconfirmation 为空：Status 设为 `complete`，记录当前 Version 的 Audit 通过，明确流程结束。
- 需要返工：Owner Skill 设为 `audit`，Status 设为 `audit-fixes-required`，记录阻塞问题和缺失验证，返回 `/audit replan <当前 Plan Ref>`。
- 需要人类决策：同时把明确决策点写入 Needs Reconfirmation，不得替人作答或只写在聊天中；后续必须经过 Audit Reconfirmation。

Needs Reconfirmation 非空时不得判定通过或设为 `complete`。

只输出阻塞问题，不罗列无关小问题。展示结论、真实 Plan Ref 和下一步后停止。

## `/audit replan [Plan Ref]`

首次调用要求 Status 为 `audit-fixes-required`；后续要求 Owner Skill 为 `audit`，Status 为 `review-pending`、`replan-required`、`reconfirmation-pending` 或 `ready-for-approval`。

- Needs Reconfirmation 为空：把 Current Plan 更新为精简返工范围，增加 Version，记录变化，重置 Review Status 和 Delivery Status，Owner Skill 设为 `audit`、Status 设为 `review-pending`。
- Needs Reconfirmation 非空：按 Reconfirmation 协议准备或修正待提交 Replan，Version 不变。

展示短摘要、真实 Plan Ref 和合法下一步后停止。普通 replan 下一步进入 `/plan-check <当前 Plan Ref>`。

## `/audit confirm [Plan Ref]`

仅当当前消息精确为 `/audit confirm <当前 Plan Ref>` 时执行。要求 Owner Skill 为 `audit`、Status 为 `reconfirmation-pending`，并存在对应当前 Version 的待提交 Replan。

提交待提交 Replan，清除已解决的确认项，增加 Version 并记录变化，重置 Review Status 和 Delivery Status。仍有未解决项时 Status 设为 `replan-required`，下一步继续 `/audit replan <新 Plan Ref>`；全部解决后 Status 设为 `review-pending`，下一步进入 `/plan-check <新 Plan Ref>`。confirm 只提交 Replan，不代表返工批准。

## `/audit approve [Plan Ref]`

仅当当前消息精确为 `/audit approve <当前 Plan Ref>` 时执行。要求：

- Owner Skill 为 `audit`，Status 为 `ready-for-approval`。
- Needs Reconfirmation 为空。
- 当前 Version 的 plan-check 已通过。
- Frontend Impact 为 `yes` 时，当前 Version 的 design-check 已通过；为 `no` 时已标记不适用。
- Frontend Impact 不得为 `unknown`。

执行前再次核对 Requirement Baseline、Current Plan 和 Unchanged Scope。若返工需要新增决策、改变基线或扩大范围，停止写入并仅撤销本轮产生的未完成改动，把决策点写入 Needs Reconfirmation，Status 设为 `replan-required`，下一步返回 `/audit replan <当前 Plan Ref>`。

完成后 Status 设为 `implemented`，记录当前 Version 的 approval、implementation 和验证结果。返回 `/audit <当前 Plan Ref>` 后停止。
