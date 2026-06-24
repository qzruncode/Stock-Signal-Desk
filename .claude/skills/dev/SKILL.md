---
name: dev
description: Use when the user invokes /dev with a requirement or an accepted Human Plan for a feature, behavior change, refactor, or other development work.
---

# Dev

所有产出使用简体中文。代码标识、路径、API 名称和必须保留的用户原文不翻译。

## Human Plan

Human Plan 的唯一目标是让人快速判断：需求是否准确、方向是否合理、范围是否可控。

- 沿用已有 Plan Ref；没有时才创建新 Plan。
- 文件只保留当前方案和简短版本摘要，不追加完整旧 Plan。
- Requirement Baseline、Confirmed Decisions 和 Unchanged Scope 不得静默改变。
- 不写代码实现、逐文件修改、类名函数名清单、测试命令或 AI 执行步骤。
- Current Plan 只描述业务方向、影响范围、用户行为、现有代码融入方式、关键边界和验收结果。
- 方案过大时拆分为多个可独立审核、开发和验证的阶段。

Plan 文件只包含：Plan ID、Version、Status、Frontend Impact、Requirement Baseline、Confirmed Decisions、Current Plan、Changes Since Last Plan、Unchanged Scope、Needs Reconfirmation、Review Status、Delivery Status、Revision Notes。

如果 Plan Ref 的版本不是当前 Version，停止处理。每次输出都返回当前 Plan Ref。

## `/dev xxx`

读取需求或 Plan Ref，检查现有代码后生成当前开发 Human Plan。

如果来自 `/idea`、`/code-scan` 或 `/arch-check`，继续使用同一个 Plan ID 和文件。增加 Version，更新 Current Plan，不新建开发 Plan。

输出后等待 `/plan-check <Plan Ref>`；Frontend Impact 为 `yes` 或 `unknown` 时，再执行 `/design-check <Plan Ref>`。

## `/dev replan [Plan Ref]`

读取当前 Plan 和检查建议，只更新需要调整的部分。

增加 Version，填写简短的 Changes Since Last Plan，重置 Review Status。不得借 replan 扩大需求或改写 Requirement Baseline。

## `/dev approve [Plan Ref]`

只执行当前版本。执行前确认：

- Status 为 `ready-for-approval`
- Needs Reconfirmation 为空
- 当前版本 `/plan-check` 已通过
- 有前端影响时，当前版本 `/design-check` 已通过

复杂任务可以生成 AI 内部执行计划，但不得写回 Human Plan，也不得改变已批准范围。

完成后只在 Delivery Status 记录简短结果，并进入 `/audit <Plan Ref>`。
