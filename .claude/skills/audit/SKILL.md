---
name: audit
description: Use when the user invokes /audit or asks to review completed AI-written changes against the approved requirement, existing codebase, and engineering quality.
---

# Audit

所有产出使用简体中文。代码标识、路径、API 名称、错误信息和引用原文不翻译。

## `/audit [Plan Ref]`

只审查当前 Plan 已批准并已实现的版本。深入检查代码，但输出按严重程度压缩，只保留需要人类决策或必须修复的问题。

如果 Plan Ref 的版本不是当前 Version，或当前版本没有 approval、implementation 记录，停止处理。

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

通过时更新 Delivery Status 的 Audit 为 `通过`。需要修复时更新为 `需要修复`，进入 `/audit replan <Plan Ref>`。

## `/audit replan [Plan Ref]`

在同一文件中把 Current Plan 更新为精简修复方案，增加 Version，仅纳入确认要修的审计问题。

保留 Requirement Baseline，不复制完整审计报告。之后重新执行 `/plan-check` 和必要的 `/design-check`。

## `/audit approve [Plan Ref]`

只执行当前 audit-fix 版本。要求 Status 为 `ready-for-approval`、Needs Reconfirmation 为空，并通过当前版本所需检查。

修复后简要更新 Delivery Status，再次执行 `/audit <Plan Ref>`。
