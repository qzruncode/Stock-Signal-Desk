---
name: bug-fix
description: Use when the user invokes /bug-fix after a concrete bug and its affected behavior have already been identified.
---

# Bug Fix

所有产出使用简体中文。代码标识、路径、API 名称、错误信息和必须保留的用户原文不翻译。

## 源码写入门禁

- 只有明确调用 `/bug-fix approve [Plan Ref]` 时才允许修改项目源码。
- `/bug-fix xxx` 和 `/bug-fix replan [Plan Ref]` 只能读取代码、定位影响并创建或更新 Human Plan。
- 即使用户描述中包含“修复”“解决”“直接改”等词，只要命令不是 `approve`，也不得修改源码。
- 输出 Human Plan 后立即停止，等待检查、replan 或 approve。

## Human Plan

Bug Human Plan 只帮助人确认修复是否准确，不展开调试过程或代码实现。

- 沿用已有 Plan Ref；无关联 Plan 时才创建新文件。
- 只保留当前修复方案，不累计完整旧版本。
- 只处理已定位 Bug，不夹带重构、优化或其他问题。
- 内容只包含预期行为、异常行为、已确认根因、修复方向、影响范围、回归风险和验收结果。
- 不写代码片段、逐文件改动、调试日志全文或执行步骤。

Plan 文件只包含：Plan ID、Version、Owner Skill、Status、Frontend Impact、Requirement Baseline、Confirmed Decisions、Current Plan、Changes Since Last Plan、Unchanged Scope、Needs Reconfirmation、Review Status、Delivery Status、Revision Notes。

如果 Plan Ref 的版本不是当前 Version，停止处理。每次输出都返回当前 Plan Ref。

## `/bug-fix xxx`

为已定位 Bug 生成简短 Human Plan，不修改代码。

没有 Plan Ref 时创建 `docs/human-plans/HP-YYYYMMDD-HHMM-<topic>.md`；有 Plan Ref 时继续使用同一个 Plan ID 和文件。

设置 Owner Skill 为 `bug-fix`、Status 为 `review-pending`，并把当前版本的 Review Status 和 Delivery Status 重置为待处理。

输出后等待 `/plan-check <Plan Ref>`；涉及前端行为时，再执行 `/design-check <Plan Ref>`。

## `/bug-fix replan [Plan Ref]`

在同一文件中更新当前修复方案，增加 Version，只记录本轮变化。

保持 Owner Skill 为 `bug-fix`，Status 重置为 `review-pending`，不得沿用旧版本检查和交付结果。不得改变预期行为或扩大 Bug 范围；必要变化放入 Needs Reconfirmation。

## `/bug-fix approve [Plan Ref]`

只执行当前版本。要求 Owner Skill 为 `bug-fix`、Status 为 `ready-for-approval`、Needs Reconfirmation 为空，并且 Review Status 中当前 Version 已通过所需检查。

完成后把 Status 设为 `implemented`，在 Delivery Status 记录当前 Version 的 approval、implementation、根因和验证结果，然后进入 `/audit <Plan Ref>`。
