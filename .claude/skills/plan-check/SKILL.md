---
name: plan-check
description: Use when the user invokes /plan-check or asks for architectural and code-impact review of a Human Plan before implementation.
---

# Plan Check

所有产出使用简体中文。代码标识、路径、API 名称和引用原文不翻译。

禁止修改项目源码和其他项目文件，只允许读取代码，并在 `docs/human-plans/` 下的当前 Plan 中更新 Review Status 与 Status。

每次停止前，根据当前 Plan 的 Owner Skill、Status、Needs Reconfirmation 和本技能流程说明下一步，并给出当前允许执行的完整命令。命令必须带入真实 Plan Ref，不留占位符。只提示，不代用户执行下一步。

## `/plan-check [Plan Ref]`

深入检查当前版本，但只输出人类需要处理的结论，不展示完整分析过程。

Plan Ref 固定为 `<Plan 文件路径>@v<Version>`。缺少版本或与文件中的当前 Version 不一致时停止处理。

Needs Reconfirmation 不为空时不得判定通过。

Owner Skill 必须是 `dev`、`bug-fix` 或 `audit`，Status 必须是 `review-pending`；否则停止检查，并返回当前阶段的正确下一步。

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

将结果更新到当前 Plan 的 Review Status，不创建新 Plan，不追加长篇 Review Ledger，不修改 Requirement Baseline、Confirmed Decisions、Current Plan、Unchanged Scope 或 Delivery Status。

Review Status 必须记录本次结果对应的 Version；旧 Version 的结果无效。

需要调整时，返回当前阶段对应的 replan：

- Owner Skill 为 `dev`：`/dev replan <当前 Plan Ref>`
- Owner Skill 为 `bug-fix`：`/bug-fix replan <当前 Plan Ref>`
- Owner Skill 为 `audit`：`/audit replan <当前 Plan Ref>`

需要 replan 时把 Status 设为 `replan-required`。

通过后，有前端影响或 Frontend Impact 为 `unknown` 时保持 Status 为 `review-pending`，下一步只允许执行 `/design-check <当前 Plan Ref>`。

Frontend Impact 为 `no` 时，把当前 Version 的 design-check 标记为 `不适用`，Status 设为 `ready-for-approval`，并按 Owner Skill 返回 `/dev approve <当前 Plan Ref>`、`/bug-fix approve <当前 Plan Ref>` 或 `/audit approve <当前 Plan Ref>`。

更新 Plan、展示结论和当前 Plan Ref 后停止，不得自动调用下一技能。
