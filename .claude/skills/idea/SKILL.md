---
name: idea
description: Use when the user invokes /idea or has a fuzzy thought, vague problem, product concern, or goal that is not yet a concrete requirement.
---

# Idea

所有产出使用简体中文。代码标识、路径、API 名称和必须保留的用户原文不翻译。

禁止修改项目源码和其他项目文件，只允许写入 `docs/human-plans/` 下的当前 Human Plan。

## Human Plan

Human Plan 用于让人快速审核，不是给 AI 执行的技术清单。

- 一个需求只维护一个 Plan 文件和 Plan ID。
- Plan 文件只保留当前方案，不累计完整旧版本。
- Replan 更新当前方案并增加版本号，只记录本轮变化。
- Requirement Baseline 未经人类明确确认不得改变。
- 不写代码片段、逐文件改动、底层实现步骤、测试命令或长篇分析。
- 内容无法保持简洁时，拆分需求，只规划当前可审核阶段。

Plan 文件固定为：

- Plan ID、Version、Owner Skill、Status、Frontend Impact
- Requirement Baseline
- Confirmed Decisions
- Current Plan
- Changes Since Last Plan
- Unchanged Scope
- Needs Reconfirmation
- Review Status
- Delivery Status
- Revision Notes

Revision Notes 每个版本只保留一句变化摘要；多轮后合并较早记录。

Review Status 只保留当前版本的 `plan-check` 和 `design-check` 结果；Delivery Status 只保留当前版本的 approval、implementation 和 audit 结果，结果必须标明 Version。

凡命令引用已有 Plan，Plan Ref 固定为 `<Plan 文件路径>@v<Version>`；缺少版本或与文件中的当前 Version 不一致时停止处理。新建 Plan 不要求输入 Plan Ref。每次输出都返回当前 Plan Ref。

写入 Plan 文件后，必须在聊天中直接展示简洁的 Requirement Baseline、Confirmed Decisions、Current Plan、Unchanged Scope、Needs Reconfirmation、Status 和 Plan Ref。不得只显示预览入口或只说已生成。

## `/idea xxx`

创建 `docs/human-plans/HP-YYYYMMDD-HHMM-<topic>.md`，设置 Owner Skill 为 `idea`、Status 为 `draft`，把模糊想法整理为简短需求：

- 要解决的问题和目标用户
- 当前痛点和预期结果
- 推荐方向和关键取舍
- 最小可用范围
- 已确认与待确认决策

不要写代码。完整展示 Human Plan 后停止，并给出 Plan Ref 和下一步。

## `/idea replan [Plan Ref]`

在同一文件中更新 Current Plan，增加 Version，保留 Requirement Baseline，并简要填写 Changes Since Last Plan。

只在用户明确调用 `/idea replan <当前 Plan Ref>` 时更新；普通自然语言反馈不得触发写入。要求 Owner Skill 为 `idea`、Status 为 `draft`。

保持 Owner Skill 为 `idea`、Status 为 `draft`。待确认的目标变化放入 Needs Reconfirmation。重新展示 Human Plan 后停止；需求确认后只返回 `/dev <当前 Plan Ref>`，等待用户显式调用。
