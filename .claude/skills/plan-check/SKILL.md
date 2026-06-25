---
name: plan-check
description: Use when the user invokes /plan-check or asks for architectural and code-impact review of a Human Plan before implementation.
---

# Plan Check

所有产出使用简体中文。代码标识、路径、API 名称和引用原文不翻译。

只允许读取项目，并在当前 Plan 中更新 Review Status、Status 和确实需要人类决策的 Needs Reconfirmation。禁止修改其他字段或项目文件。

Owner Skill 决定后续命令前缀：`dev` 对应 `/dev`，`bug-fix` 对应 `/bug-fix`，`audit` 对应 `/audit`。

## `/plan-check [Plan Ref]`

Plan Ref 必须是 `<Plan 文件路径>@v<Version>` 且匹配当前 Version，否则停止且不写入。

按以下顺序检查前置状态：

1. Owner Skill 必须是 `dev`、`bug-fix` 或 `audit`。
2. Status 为 `reconfirmation-pending` 时停止检查：理解正确返回对应 Owner 的 `confirm`，理解不正确返回对应 Owner 的 `replan`。
3. Needs Reconfirmation 非空时停止检查，返回对应 Owner 的 `replan`。
4. Status 必须是 `review-pending`，否则根据 Owner Skill 和 Status 返回当前合法下一步。

检查当前版本是否：

- 保留 Requirement Baseline、Confirmed Decisions 和 Unchanged Scope。
- 复用并融入现有代码结构，没有平行逻辑、重复造轮子或错误抽象。
- 覆盖模块、接口、状态、数据、缓存、存储和依赖影响。
- 覆盖用户行为、边界、兼容性、性能、安全、可靠性和数据一致性风险。
- 足够具体但没有落入代码实现清单。

输出仅包含结论、阻塞问题、必须修改的 Plan 内容和需要人类决策的事项。通过时不重复完整 Plan。

## 结果

- 需要 AI 调整且无需人类决策：写入当前 Version 的 Review Status，把 Status 设为 `replan-required`，返回对应 Owner 的 `replan`。
- 需要人类决策：把明确决策点写入 Needs Reconfirmation，不得替人作答或只写在聊天中；Status 设为 `replan-required`，返回对应 Owner 的 `replan`，后续必须经过该 Owner 的 Reconfirmation。
- 通过且 Frontend Impact 为 `yes` 或 `unknown`：记录当前 Version 通过，Status 保持 `review-pending`，返回 `/design-check <当前 Plan Ref>`。
- 通过且 Frontend Impact 为 `no`：记录 plan-check 通过和 design-check 不适用，Status 设为 `ready-for-approval`，返回对应 Owner 的 `approve`。

Review Status 必须绑定当前 Version，旧版本结果无效。展示结论、真实 Plan Ref 和完整下一步命令后停止，不得自动调用下一技能。
