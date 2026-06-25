---
name: dev
description: Use when the user invokes /dev with a requirement or an accepted Human Plan for a feature, behavior change, refactor, or other development work.
---

# Dev

所有产出使用简体中文。代码标识、路径、API 名称和必须保留的用户原文不翻译。

## 权限

- `/dev xxx`、`/dev replan` 和 `/dev confirm` 只允许读取项目并写入当前 Human Plan。
- 只有当前消息精确为 `/dev approve <当前 Plan Ref>` 时才允许修改源码。
- 自然语言中的“实现”“修改”“可以”“按这个做”等都不是 approve。
- approve 前除当前 Plan 外禁止任何写入；输出 Plan 后必须停止。

## Plan 约束

- 始终沿用同一个 Plan 文件和 Plan ID；只有无 Plan Ref 的新需求才创建文件。
- Human Plan 面向人类审核，不写代码、逐文件改动、测试命令或 AI 执行步骤。
- Requirement Baseline、Confirmed Decisions 和 Unchanged Scope 不得静默改变。
- 固定字段为：Plan ID、Version、Owner Skill、Status、Frontend Impact、Requirement Baseline、Confirmed Decisions、Current Plan、Changes Since Last Plan、Unchanged Scope、Needs Reconfirmation、Review Status、Delivery Status、Revision Notes。
- Owner Skill 为 `dev`；Status 只使用 `review-pending`、`replan-required`、`reconfirmation-pending`、`ready-for-approval` 或 `implemented`。
- Frontend Impact 只使用 `yes`、`no` 或 `unknown`。
- Plan Ref 固定为 `<Plan 文件路径>@v<Version>`。引用版本不一致时停止且不写入，并返回当前 Plan Ref。
- 任一命令的 Owner、Status 或前置条件不满足时停止且不写入，并根据当前 Plan 返回合法下一步。

## Reconfirmation

Needs Reconfirmation 非空时，`replan` 只能准备待提交 Replan，不能直接更新正式 Plan：

- 使用当前消息中的人类答复，不从更早对话猜测。
- 在 Needs Reconfirmation 中保留相关用户原文、AI 理解和拟应用变化。
- 不清除确认项，不修改正式 Plan，不增加 Version，不恢复检查状态。
- Status 设为 `reconfirmation-pending`，完整展示待提交 Replan 后停止。

当前消息没有可用于对应确认项的答复时，不写入任何内容，只展示待确认事项并要求用户在 `/dev replan <当前 Plan Ref>` 后补充答复。

理解不正确时继续 `/dev replan <当前 Plan Ref>` 修正待提交 Replan。只有精确的 `/dev confirm <当前 Plan Ref>` 才能提交；自然语言肯定不算 confirm。未经 confirm，不得 check 或 approve。

## `/dev xxx`

读取需求和现有代码，生成简洁开发 Human Plan。

- 无 Plan Ref：创建 Version 1 的 Plan。
- 来自 `idea`、`code-scan` 或 `arch-check`：要求 Status 为 `draft`、Needs Reconfirmation 为空；沿用文件和 Plan ID，增加 Version。执行 `/dev <Plan Ref>` 表示人类接受来源 Plan 当前基线，开发规划只能补充代码影响、融入方式、边界和验收结果。

设置 Owner Skill 为 `dev`，重置当前版本的 Review Status 和 Delivery Status。Needs Reconfirmation 为空时 Status 设为 `review-pending`，下一步只允许 `/plan-check <当前 Plan Ref>`；非空时 Status 设为 `replan-required`，下一步只允许 `/dev replan <当前 Plan Ref>` 并要求补充对应答复。直接展示 Plan 和真实 Plan Ref 后停止。

## `/dev replan [Plan Ref]`

要求 Owner Skill 为 `dev`，Status 为 `review-pending`、`replan-required`、`reconfirmation-pending` 或 `ready-for-approval`。

- Needs Reconfirmation 为空：只调整需要修改的 Plan 内容，增加 Version，记录变化，重置 Review Status 和 Delivery Status，Status 设为 `review-pending`。
- Needs Reconfirmation 非空：按 Reconfirmation 协议准备或修正待提交 Replan，Version 不变。

不得借 replan 扩大 Requirement Baseline。展示当前 Plan、真实 Plan Ref 和合法下一步后停止。

## `/dev confirm [Plan Ref]`

仅当当前消息精确为 `/dev confirm <当前 Plan Ref>` 时执行。要求 Owner Skill 为 `dev`、Status 为 `reconfirmation-pending`，并存在对应当前 Version 的待提交 Replan。

提交待提交 Replan，清除已解决的确认项，增加 Version 并记录变化，重置 Review Status 和 Delivery Status。仍有未解决项时 Status 设为 `replan-required`，下一步继续 `/dev replan <新 Plan Ref>`；全部解决后 Status 设为 `review-pending`，下一步进入 `/plan-check <新 Plan Ref>`。confirm 只提交 Replan，不代表开发批准。

## `/dev approve [Plan Ref]`

仅当当前消息精确为 `/dev approve <当前 Plan Ref>` 时执行。要求：

- Owner Skill 为 `dev`，Status 为 `ready-for-approval`。
- Needs Reconfirmation 为空。
- 当前 Version 的 plan-check 已通过。
- Frontend Impact 为 `yes` 时，当前 Version 的 design-check 已通过；为 `no` 时已标记不适用。
- Frontend Impact 不得为 `unknown`。

执行前再次核对 Requirement Baseline、Current Plan 和 Unchanged Scope。若实现需要新增决策、改变基线或扩大范围，停止写入并仅撤销本轮产生的未完成改动，把决策点写入 Needs Reconfirmation，Status 设为 `replan-required`，下一步返回 `/dev replan <当前 Plan Ref>`。

完成后 Status 设为 `implemented`，在 Delivery Status 记录当前 Version 的 approval、implementation 和验证结果。返回 `/audit <当前 Plan Ref>` 后停止。
