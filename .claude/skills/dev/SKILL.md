---
name: dev
description: Use when the user invokes /dev with a requirement or an accepted Human Plan for a feature, behavior change, refactor, or other development work.
---

# Dev

所有产出使用简体中文。代码标识、路径、API 名称和必须保留的用户原文不翻译。

## 源码写入门禁

- 只有当前用户消息去除首尾空白后，仅包含 `/dev approve <当前 Plan Ref>` 时，才允许修改项目源码。
- `/dev xxx` 和 `/dev replan [Plan Ref]` 只能读取代码并创建或更新 Human Plan。
- 不得把需求描述中的“实现”“修改”“开发”等词视为执行批准。
- 用户对问题的回答以及“好”“可以”“就这样”“按这个做”等自然语言都只是 Plan 反馈，不是 approve。
- 普通自然语言反馈也不得自动更新 Plan；只有明确的 `/dev replan <当前 Plan Ref>` 才能重写 Plan。
- 在收到新的、明确的 `/dev approve <当前 Plan Ref>` 消息前，只允许写入 `docs/human-plans/` 下的当前 Plan 文件，禁止其他写入。
- 输出 Human Plan 后立即停止，等待检查、replan 或 approve。

## Human Plan

Human Plan 的唯一目标是让人快速判断：需求是否准确、方向是否合理、范围是否可控。

- 沿用已有 Plan Ref；没有时才创建新 Plan。
- 文件只保留当前方案和简短版本摘要，不追加完整旧 Plan。
- Requirement Baseline、Confirmed Decisions 和 Unchanged Scope 不得静默改变。
- 不写代码实现、逐文件修改、类名函数名清单、测试命令或 AI 执行步骤。
- Current Plan 只描述业务方向、影响范围、用户行为、现有代码融入方式、关键边界和验收结果。
- 方案过大时拆分为多个可独立审核、开发和验证的阶段。
- 不在生成 Plan 前发起会让流程继续执行的交互式提问；不确定项写入 Needs Reconfirmation。
- 用户补充待确认信息时，要求使用 `/dev replan <当前 Plan Ref>`；更新并重新展示 Human Plan 后停止，仍需重新检查和显式 approve。

Plan 文件只包含：Plan ID、Version、Owner Skill、Status、Frontend Impact、Requirement Baseline、Confirmed Decisions、Current Plan、Changes Since Last Plan、Unchanged Scope、Needs Reconfirmation、Review Status、Delivery Status、Revision Notes。

Frontend Impact 只使用 `yes`、`no` 或 `unknown`。

凡命令引用已有 Plan，Plan Ref 固定为 `<Plan 文件路径>@v<Version>`；缺少版本或与文件中的当前 Version 不一致时停止处理。直接创建 Plan 不要求输入 Plan Ref。每次输出都返回当前 Plan Ref。

写入 Plan 文件后，必须在聊天中直接展示简洁的 Requirement Baseline、Confirmed Decisions、Current Plan、Unchanged Scope、Needs Reconfirmation、Status 和 Plan Ref。不得只显示预览入口、只说已生成，或在展示前进入实现。

每次停止前，根据当前 Plan 的 Owner Skill、Status、Needs Reconfirmation 和本技能流程说明下一步，并给出当前允许执行的完整命令。命令必须带入真实 Plan Ref，不留占位符；有多个合法选择时说明用途，无需继续时说明结束。只提示，不代用户执行下一步。

## `/dev xxx`

读取需求或 Plan Ref，检查现有代码后生成当前开发 Human Plan。

如果来自 `/idea`、`/code-scan` 或 `/arch-check`，继续使用同一个 Plan ID 和文件。增加 Version，更新 Current Plan，不新建开发 Plan。

如果没有 Plan Ref，创建 `docs/human-plans/HP-YYYYMMDD-HHMM-<topic>.md`。

用户显式执行 `/dev <Plan Ref>`，即确认来源 Plan 当前版本的 Requirement Baseline、Confirmed Decisions 和 Unchanged Scope。开发规划只能在该基线上补充代码影响和实施边界，不得重新解释或改写需求。

接收已有 Plan 时要求 Owner Skill 为 `idea`、`code-scan` 或 `arch-check`，Status 为 `draft`，Needs Reconfirmation 为空；否则停止并返回原 Owner Skill 的 replan 命令。直接创建开发 Plan 时从 Version 1 开始。

设置 Owner Skill 为 `dev`、Status 为 `review-pending`，并把当前版本的 Review Status 和 Delivery Status 重置为待处理。

完整展示 Human Plan 后停止，下一步只允许执行 `/plan-check <当前 Plan Ref>`，等待用户显式调用。不得自动执行 check。

## `/dev replan [Plan Ref]`

读取当前 Plan 和检查建议，只更新需要调整的部分。

只在用户明确调用 `/dev replan <当前 Plan Ref>` 时更新。要求 Owner Skill 为 `dev`，Status 为 `review-pending`、`replan-required` 或 `ready-for-approval`。

增加 Version，填写简短的 Changes Since Last Plan，重置 Review Status。不得借 replan 扩大需求或改写 Requirement Baseline。

保持 Owner Skill 为 `dev`，Status 重置为 `review-pending`，当前版本的 Review Status 和 Delivery Status 都重置为待处理。重新展示 Human Plan 后停止。

## `/dev approve [Plan Ref]`

仅当当前用户消息去除首尾空白后只包含 `/dev approve <当前 Plan Ref>` 时执行当前版本。执行前确认：

- Status 为 `ready-for-approval`
- Owner Skill 为 `dev`
- Needs Reconfirmation 为空
- Review Status 记录当前 Version 的 `/plan-check` 已通过
- 有前端影响时，Review Status 记录当前 Version 的 `/design-check` 已通过

复杂任务可以生成 AI 内部执行计划，但不得写回 Human Plan，也不得改变已批准范围。

源码写入前再次核对 Requirement Baseline、Current Plan 和 Unchanged Scope。发现需要新增决策、扩大范围或改变已批准方案时不得自行处理；停止执行，把事项写入 Needs Reconfirmation，将 Status 设为 `replan-required`，并返回 `/dev replan <当前 Plan Ref>`。不得保留未完成的本轮源码改动。

完成后把 Status 设为 `implemented`，在 Delivery Status 记录当前 Version 的 approval、implementation 和简短验证结果。返回 `/audit <当前 Plan Ref>` 后停止，等待用户显式调用。
