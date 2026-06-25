---
name: audit
description: Use when the user invokes /audit or asks to review completed AI-written changes against the approved requirement, existing codebase, and engineering quality.
---

# Audit

所有产出使用简体中文。代码标识、路径、API 名称、错误信息和引用原文不翻译。

## 源码写入门禁

- 只有当前用户消息去除首尾空白后，仅包含 `/audit approve <当前 Plan Ref>` 时，才允许修改项目源码。
- `/audit [Plan Ref]` 只能审查代码并更新审查结论。
- `/audit replan [Plan Ref]` 只能更新 Human Plan。
- 用户对审计问题的回答以及“好”“可以”“就这样”“按这个做”等自然语言都只是 Plan 反馈，不是 approve。
- 普通自然语言反馈也不得自动更新 Plan；只有明确的 `/audit replan <当前 Plan Ref>` 才能重写 Plan。
- 在收到新的、明确的 `/audit approve <当前 Plan Ref>` 消息前，只允许写入 `docs/human-plans/` 下的当前 Plan 文件，禁止其他写入。
- 审计发现问题后不得立即修复，必须等待 replan、检查和 approve。

每次停止前，根据当前 Plan 的 Owner Skill、Status、Needs Reconfirmation 和本技能流程说明下一步，并给出当前允许执行的完整命令。命令必须带入真实 Plan Ref，不留占位符；有多个合法选择时说明用途，无需继续时说明结束。只提示，不代用户执行下一步。

## `/audit [Plan Ref]`

只审查当前 Plan 已批准并已实现的版本。深入检查代码，但输出按严重程度压缩，只保留需要人类决策或必须修复的问题。

Plan Ref 固定为 `<Plan 文件路径>@v<Version>`。缺少版本、与文件中的当前 Version 不一致、Status 不是 `implemented`，或 Delivery Status 没有当前 Version 的 approval、implementation 记录时，停止处理。

Owner Skill 必须是 `dev`、`bug-fix` 或 `audit`。

检查：

- Requirement Baseline 和批准方案是否真正实现
- 新代码是否复用并融入现有结构
- 正确性、边界情况、兼容性和回归
- 架构、抽象、重复、复杂度和维护性
- 前端性能、交互、响应式和样式一致性
- 后端契约、校验、错误处理和异步流程
- 数据一致性、缓存、存储、幂等和过期数据
- 安全、重试、超时、降级和可用性
- 验证是否充分

输出只包含：

- 结论：`通过` 或 `需要修复`
- 按严重程度排列的阻塞问题
- 缺失的必要验证
- 下一步

不罗列无关的小问题，不重复完整 Human Plan。

通过时把 Status 设为 `complete`，并在 Delivery Status 记录当前 Version 的 Audit 为 `通过`。需要修复时把 Status 设为 `audit-fixes-required`，记录当前 Version 的 Audit 为 `需要修复`，下一步只允许执行 `/audit replan <当前 Plan Ref>`。

更新 Plan、展示结论、当前 Plan Ref 和唯一下一步后停止，不得自动 replan 或修复。

## `/audit replan [Plan Ref]`

在同一文件中把 Current Plan 更新为精简修复方案，增加 Version，仅纳入确认要修的审计问题。

只在用户明确调用 `/audit replan <当前 Plan Ref>` 时更新。首次创建修复计划要求 Status 为 `audit-fixes-required`，并有当前 Version 的 Audit `需要修复` 记录；后续调整要求 Owner Skill 为 `audit`，Status 为 `review-pending`、`replan-required` 或 `ready-for-approval`。

设置 Owner Skill 为 `audit`、Status 为 `review-pending`，清空新版本的 Review Status 和 Delivery Status。保留 Requirement Baseline、Confirmed Decisions 和 Unchanged Scope，不复制完整审计报告。

写入后必须在聊天中直接展示简洁的 Requirement Baseline、Current Plan、Unchanged Scope、Needs Reconfirmation、Status 和 Plan Ref，下一步只允许执行 `/plan-check <当前 Plan Ref>`。随后停止，不得只显示预览入口、根据自然语言反馈直接修复或自动执行 check。

## `/audit approve [Plan Ref]`

仅当当前用户消息去除首尾空白后只包含 `/audit approve <当前 Plan Ref>` 时执行当前 audit-fix 版本。要求 Owner Skill 为 `audit`、Status 为 `ready-for-approval`、Needs Reconfirmation 为空，Review Status 中当前 Version 的 `/plan-check` 已通过，并且有前端影响时当前 Version 的 `/design-check` 已通过。

源码写入前再次核对 Requirement Baseline、Current Plan 和 Unchanged Scope。发现修复需要新增决策、扩大范围或改变已批准方案时不得自行处理；停止执行，把事项写入 Needs Reconfirmation，将 Status 设为 `replan-required`，并返回 `/audit replan <当前 Plan Ref>`。不得保留未完成的本轮源码改动。

修复后把 Status 设为 `implemented`，在 Delivery Status 记录当前 Version 的 approval、implementation 和简短验证结果。返回 `/audit <当前 Plan Ref>` 后停止，等待用户显式调用。
