---
name: bug-fix
description: Use when the user invokes /bug-fix after a concrete bug and its affected behavior have already been identified.
---

# Bug Fix

所有产出使用简体中文。代码标识、路径、API 名称、错误信息和必须保留的用户原文不翻译。

## 源码写入门禁

- 只有当前用户消息去除首尾空白后，仅包含 `/bug-fix approve <当前 Plan Ref>` 时，才允许修改项目源码。
- `/bug-fix xxx` 和 `/bug-fix replan [Plan Ref]` 只能读取代码、定位影响并创建或更新 Human Plan。
- 即使用户描述中包含“修复”“解决”“直接改”等词，只要命令不是 `approve`，也不得修改源码。
- 用户对问题的回答以及“好”“可以”“就这样”“按这个做”等自然语言都只是 Plan 反馈，不是 approve。
- 普通自然语言反馈也不得自动更新 Plan；只有明确的 `/bug-fix replan <当前 Plan Ref>` 才能重写 Plan。
- 在收到新的、明确的 `/bug-fix approve <当前 Plan Ref>` 消息前，只允许写入 `docs/human-plans/` 下的当前 Plan 文件，禁止其他写入。
- 输出 Human Plan 后立即停止，等待检查、replan 或 approve。

## Human Plan

Bug Human Plan 只帮助人确认修复是否准确，不展开调试过程或代码实现。

- 一个 Bug 只维护一个 Plan 文件；已有 Bug Plan 只能通过 replan 更新。
- 只保留当前修复方案，不累计完整旧版本。
- 只处理已定位 Bug，不夹带重构、优化或其他问题。
- 内容只包含预期行为、异常行为、已确认根因、修复方向、影响范围、回归风险和验收结果。
- 不写代码片段、逐文件改动、调试日志全文或执行步骤。
- 不在生成 Plan 前发起会让流程继续执行的交互式提问；不确定项写入 Needs Reconfirmation。
- 用户补充待确认信息时，要求使用 `/bug-fix replan <当前 Plan Ref>`；更新并重新展示 Human Plan 后停止，仍需重新检查和显式 approve。

Plan 文件只包含：Plan ID、Version、Owner Skill、Status、Frontend Impact、Requirement Baseline、Confirmed Decisions、Current Plan、Changes Since Last Plan、Unchanged Scope、Needs Reconfirmation、Review Status、Delivery Status、Revision Notes。

凡命令引用已有 Plan，Plan Ref 固定为 `<Plan 文件路径>@v<Version>`；缺少版本或与文件中的当前 Version 不一致时停止处理。新建 Bug Plan 不要求输入 Plan Ref。每次输出都返回当前 Plan Ref。

写入 Plan 文件后，必须在聊天中直接展示简洁的 Requirement Baseline、Confirmed Decisions、Current Plan、Unchanged Scope、Needs Reconfirmation、Status 和 Plan Ref。不得只显示预览入口、只说已生成，或在展示前进入实现。

## `/bug-fix xxx`

为已定位 Bug 生成简短 Human Plan，不修改代码。

创建 `docs/human-plans/HP-YYYYMMDD-HHMM-<topic>.md`，从 Version 1 开始。若命令携带已有 Plan Ref，停止并根据其 Owner Skill 和 Status 返回正确的 replan 命令，不得接管或覆盖其他 Plan。

设置 Owner Skill 为 `bug-fix`、Status 为 `review-pending`，并把当前版本的 Review Status 和 Delivery Status 重置为待处理。

完整展示 Human Plan 后停止，只返回 `/plan-check <当前 Plan Ref>`，等待用户显式调用。不得自动执行 check。

## `/bug-fix replan [Plan Ref]`

在同一文件中更新当前修复方案，增加 Version，只记录本轮变化。

只在用户明确调用 `/bug-fix replan <当前 Plan Ref>` 时更新。要求 Owner Skill 为 `bug-fix`，Status 为 `review-pending`、`replan-required` 或 `ready-for-approval`。

保持 Owner Skill 为 `bug-fix`，Status 重置为 `review-pending`，当前版本的 Review Status 和 Delivery Status 都重置为待处理。不得改变预期行为或扩大 Bug 范围；必要变化放入 Needs Reconfirmation。重新展示 Human Plan 后停止。

## `/bug-fix approve [Plan Ref]`

仅当当前用户消息去除首尾空白后只包含 `/bug-fix approve <当前 Plan Ref>` 时执行当前版本。要求 Owner Skill 为 `bug-fix`、Status 为 `ready-for-approval`、Needs Reconfirmation 为空，并且 Review Status 中当前 Version 已通过所需检查。

源码写入前再次核对 Requirement Baseline、Current Plan 和 Unchanged Scope。发现根因不成立、需要扩大范围或改变已批准方案时不得自行处理；停止执行，把事项写入 Needs Reconfirmation，将 Status 设为 `replan-required`，并返回 `/bug-fix replan <当前 Plan Ref>`。不得保留未完成的本轮源码改动。

完成后把 Status 设为 `implemented`，在 Delivery Status 记录当前 Version 的 approval、implementation、根因和验证结果。返回 `/audit <当前 Plan Ref>` 后停止，等待用户显式调用。
