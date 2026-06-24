---
name: plan-check
description: Use when the user invokes /plan-check or asks for architectural and code-impact review of a Human Plan before implementation.
---

# Plan Check

所有产出使用简体中文。代码标识、路径、API 名称和引用原文不翻译。

禁止修改项目源码，只允许读取代码并更新当前 Plan 的 Review Status。

## `/plan-check [Plan Ref]`

深入检查当前版本，但只输出人类需要处理的结论，不展示完整分析过程。

如果 Plan Ref 的版本不是当前 Version，停止处理。

Needs Reconfirmation 不为空时不得判定通过。

Owner Skill 必须是 `dev`、`bug-fix` 或 `audit`；否则停止检查，并要求先进入 `/dev` 或对应开发阶段。

检查：

- Requirement Baseline 和已确认决策是否被保留
- 方案是否真正复用并融入现有代码结构
- 是否重复造轮子、创建平行逻辑或错误抽象
- 对模块、接口、状态、数据、缓存、存储和依赖方向的影响
- 用户行为、边界情况、兼容性和回归风险
- 性能、安全、可靠性和数据一致性风险
- 是否具体到可以执行，又没有陷入代码实现细节

输出只包含：

- 结论：`通过` 或 `需要 replan`
- 阻塞执行的关键问题
- 必须修改的 Plan 内容
- 需要人类重新确认的事项

通过时只写简短结论，不重复复述 Plan。非阻塞建议不写入 Human Plan。

将结果更新到当前 Plan 的 Review Status，不创建新 Plan，不追加长篇 Review Ledger，不修改 Current Plan。

Review Status 必须记录本次结果对应的 Version；旧 Version 的结果无效。

需要调整时，返回当前阶段对应的 replan：

- Owner Skill 为 `dev`：`/dev replan <Plan Ref>`
- Owner Skill 为 `bug-fix`：`/bug-fix replan <Plan Ref>`
- Owner Skill 为 `audit`：`/audit replan <Plan Ref>`

需要 replan 时把 Status 设为 `replan-required`。

通过后，有前端影响或 Frontend Impact 为 `unknown` 时进入 `/design-check <Plan Ref>`；Frontend Impact 为 `no` 时，把当前 Version 的 design-check 标记为 `不适用`，Status 设为 `ready-for-approval`。
