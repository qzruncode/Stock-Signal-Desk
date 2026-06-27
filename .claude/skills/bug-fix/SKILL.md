---
name: bug-fix
description: Use when the user invokes /bug-fix after a concrete bug and its affected behavior have already been identified.
---

# Bug Fix

所有产出使用简体中文。代码标识、路径、API 名称、错误信息和必须保留的用户原文不翻译。

## 权限

- `/bug-fix xxx`、`/bug-fix replan` 和 `/bug-fix confirm` 只允许读取项目并写入当前 Human Plan。
- 只有当前消息精确为 `/bug-fix approve <当前 Plan Ref>` 时才允许修改源码。
- 自然语言中的“修复”“直接改”“可以”“按这个做”等都不是 approve。
- approve 前除当前 Plan 外禁止任何写入；输出 Plan 后必须停止。

## Plan 约束

- 一个已定位问题始终使用同一个 Plan 文件和 Plan ID，不接管其他 Owner 的 Plan。
- Human Plan 只描述预期行为、异常行为、已确认根因、修复方向、影响范围、回归风险和验收结果。
- 不夹带重构、优化或其他事项，不写代码、逐文件改动或执行步骤。
- 固定字段为：Plan ID、Version、Owner Skill、Status、Frontend Impact、Requirement Baseline、Confirmed Decisions、Current Plan、Changes Since Last Plan、Unchanged Scope、Needs Reconfirmation、Review Status、Delivery Status、Revision Notes。
- Human Plan 是人类与 AI 的修复需求对齐凭据，不是排查记录或实现说明。
- 固定字段必须保留；不适用字段写 `无`，不要为了填字段展开解释。
- Plan 只回答：现在什么行为错了、修完应是什么行为、明确不做什么、怎么验收。
- Current Plan 就是最终修复需求提示词：loop 多轮后，人类和 AI 已达成一致，AI 可按它执行。
- 禁止写文件路径、函数名、行号、内部实现步骤、测试命令或技术排查过程，除非这些本身就是用户要审核的需求。
- approve 后 AI 可以在内部拆解技术执行步骤；这些步骤不写回 Human Plan，除非出现新的需求决策。
- Changes Since Last Plan 只写本轮需求变化一句话，不保留多轮历史。
- Needs Reconfirmation 只写当前未解决的人类决策问题，不写分析过程或完整 replan 草稿。
- Review Status、Delivery Status 和 Revision Notes 只写短状态，不写检查报告。
- 聊天输出只展示短摘要、真实 Plan Ref 和下一步命令，不重复完整 Plan。
- Owner Skill 为 `bug-fix`；Status 只使用 `review-pending`、`replan-required`、`reconfirmation-pending`、`ready-for-approval` 或 `implemented`。
- Frontend Impact 只使用 `yes`、`no` 或 `unknown`。
- Plan Ref 固定为 `<Plan 文件路径>@v<Version>`。引用版本不一致时停止且不写入，并返回当前 Plan Ref。
- 任一命令的 Owner、Status 或前置条件不满足时停止且不写入，并根据当前 Plan 返回合法下一步。

## Reconfirmation

Needs Reconfirmation 非空时，`replan` 只能准备待提交 Replan，不能直接更新正式 Plan：

- 使用当前消息中的人类答复，不从更早对话猜测。
- 在 Needs Reconfirmation 中用短句保留人类问题、AI 理解和拟变更点。
- 不清除确认项，不修改正式 Plan，不增加 Version，不恢复检查状态。
- Status 设为 `reconfirmation-pending`，只展示待提交 Replan 的理解摘要和拟变更点后停止。

当前消息没有可用于对应确认项的答复时，不写入任何内容，只展示待确认事项并要求用户在 `/bug-fix replan <当前 Plan Ref>` 后补充答复。

理解不正确时继续 `/bug-fix replan <当前 Plan Ref>` 修正待提交 Replan。只有精确的 `/bug-fix confirm <当前 Plan Ref>` 才能提交；自然语言肯定不算 confirm。未经 confirm，不得 check 或 approve。

## `/bug-fix xxx`

为已定位问题创建 Version 1 的简洁 Human Plan。设置 Owner Skill 为 `bug-fix`，重置 Review Status 和 Delivery Status。

命令中出现已有 Plan Ref 时不得接管该 Plan；停止且根据其 Owner Skill 和 Status 返回合法下一步。

Needs Reconfirmation 为空时 Status 设为 `review-pending`，下一步只允许 `/plan-check <当前 Plan Ref>`；非空时 Status 设为 `replan-required`，下一步只允许 `/bug-fix replan <当前 Plan Ref>` 并要求补充对应答复。只展示短摘要、真实 Plan Ref 和下一步命令后停止。

## `/bug-fix replan [Plan Ref]`

要求 Owner Skill 为 `bug-fix`，Status 为 `review-pending`、`replan-required`、`reconfirmation-pending` 或 `ready-for-approval`。

- Needs Reconfirmation 为空：只调整当前修复范围，增加 Version，记录变化，重置 Review Status 和 Delivery Status，Status 设为 `review-pending`。
- Needs Reconfirmation 非空：按 Reconfirmation 协议准备或修正待提交 Replan，Version 不变。

不得改变预期行为或扩大事项范围。展示短摘要、真实 Plan Ref 和合法下一步后停止。

## `/bug-fix confirm [Plan Ref]`

仅当当前消息精确为 `/bug-fix confirm <当前 Plan Ref>` 时执行。要求 Owner Skill 为 `bug-fix`、Status 为 `reconfirmation-pending`，并存在对应当前 Version 的待提交 Replan。

提交待提交 Replan，清除已解决的确认项，增加 Version 并记录变化，重置 Review Status 和 Delivery Status。仍有未解决项时 Status 设为 `replan-required`，下一步继续 `/bug-fix replan <新 Plan Ref>`；全部解决后 Status 设为 `review-pending`，下一步进入 `/plan-check <新 Plan Ref>`。confirm 只提交 Replan，不代表修复批准。

## `/bug-fix approve [Plan Ref]`

仅当当前消息精确为 `/bug-fix approve <当前 Plan Ref>` 时执行。要求：

- Owner Skill 为 `bug-fix`，Status 为 `ready-for-approval`。
- Needs Reconfirmation 为空。
- 当前 Version 的 plan-check 已通过。
- Frontend Impact 为 `yes` 时，当前 Version 的 design-check 已通过；为 `no` 时已标记不适用。
- Frontend Impact 不得为 `unknown`。

执行前再次核对 Requirement Baseline、Current Plan 和 Unchanged Scope。若根因不成立或实现需要新增决策、改变基线或扩大范围，停止写入并仅撤销本轮产生的未完成改动，把决策点写入 Needs Reconfirmation，Status 设为 `replan-required`，下一步返回 `/bug-fix replan <当前 Plan Ref>`。

完成后 Status 设为 `implemented`，在 Delivery Status 记录当前 Version 的 approval、implementation、根因和验证结果。返回 `/audit <当前 Plan Ref>` 后停止。
