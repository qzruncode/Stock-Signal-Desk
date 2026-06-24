---
name: bug-fix
description: Use when the user invokes /bug after a concrete bug and its affected behavior have already been identified.
---

# Bug Fix

所有产出使用简体中文。代码标识、路径、API 名称、错误信息和必须保留的用户原文不翻译。

## Human Plan

Bug Human Plan 只帮助人确认修复是否准确，不展开调试过程或代码实现。

- 沿用已有 Plan Ref；无关联 Plan 时才创建新文件。
- 只保留当前修复方案，不累计完整旧版本。
- 只处理已定位 Bug，不夹带重构、优化或其他问题。
- 内容只包含预期行为、异常行为、已确认根因、修复方向、影响范围、回归风险和验收结果。
- 不写代码片段、逐文件改动、调试日志全文或执行步骤。

Plan 文件只包含：Plan ID、Version、Status、Frontend Impact、Requirement Baseline、Confirmed Decisions、Current Plan、Changes Since Last Plan、Unchanged Scope、Needs Reconfirmation、Review Status、Delivery Status、Revision Notes。

如果 Plan Ref 的版本不是当前 Version，停止处理。每次输出都返回当前 Plan Ref。

## `/bug xxx`

为已定位 Bug 生成简短 Human Plan，不修改代码。

输出后等待 `/plan-check <Plan Ref>`；涉及前端行为时，再执行 `/design-check <Plan Ref>`。

## `/bug replan [Plan Ref]`

在同一文件中更新当前修复方案，增加 Version，只记录本轮变化。

不得改变预期行为或扩大 Bug 范围；必要变化放入 Needs Reconfirmation。

## `/bug approve [Plan Ref]`

只执行当前版本。要求 Status 为 `ready-for-approval`、Needs Reconfirmation 为空，并通过当前版本所需检查。

完成后只在 Delivery Status 记录根因、修改结果和验证结果，然后进入 `/audit <Plan Ref>`。
