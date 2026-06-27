---
name: arch-check
description: Use when the user invokes /arch-check or asks whether current business logic, product behavior, code, or architecture is reinventing wheels or diverging from mature solutions.
---

# Arch Check

所有产出使用简体中文。代码标识、路径、API 名称、仓库名称、链接和引用原文不翻译。

只允许研究本地代码、成熟方案和外部参考，并写入 `docs/human-plans/` 下的当前 Human Plan，禁止修改其他项目文件。

## Plan 约束

- 一个研究范围始终使用同一个 Plan 文件和 Plan ID。
- 先检查本地代码，再使用可用的网络或 GitHub MCP；Human Plan 不记录调研流水账。
- 固定字段为：Plan ID、Version、Owner Skill、Status、Frontend Impact、Requirement Baseline、Confirmed Decisions、Current Plan、Changes Since Last Plan、Unchanged Scope、Needs Reconfirmation、Review Status、Delivery Status、Revision Notes。
- Human Plan 是人类与 AI 的需求对齐凭据，不是调研报告或技术改造计划。
- 固定字段必须保留；不适用字段写 `无`，不要为了填字段展开解释。
- Plan 只回答：当前方案哪里不合理、应该对齐成什么目标状态、明确不做什么、怎么验收。
- Current Plan 就是最终需求提示词：loop 多轮后，人类和 AI 已达成一致，AI 可按它执行。
- 成熟方案只保留与人类决策直接相关的结论和取舍，不记录搜索过程、代码结构或实现步骤。
- AI 后续执行时可自行推导技术步骤；Human Plan 只保存双方对齐后的需求契约。
- Changes Since Last Plan 只写本轮需求变化一句话，不保留多轮历史。
- Needs Reconfirmation 只写当前未解决的人类决策问题，不写分析过程或完整 replan 草稿。
- Review Status、Delivery Status 和 Revision Notes 只写短状态，不写检查报告。
- 聊天输出只展示短摘要、真实 Plan Ref 和下一步命令，不重复完整 Plan。
- Owner Skill 为 `arch-check`；Status 只使用 `draft` 或 `reconfirmation-pending`；Frontend Impact 只使用 `yes`、`no` 或 `unknown`。
- Plan Ref 固定为 `<Plan 文件路径>@v<Version>`。引用版本不一致时停止且不写入，并返回当前 Plan Ref。
- 任一命令的 Owner、Status 或前置条件不满足时停止且不写入，并根据当前 Plan 返回合法下一步。

## Reconfirmation

Needs Reconfirmation 非空时，`replan` 只能准备待提交 Replan，不能直接更新正式 Plan：

- 使用当前消息中的人类答复，不从更早对话猜测。
- 在 Needs Reconfirmation 中用短句保留人类问题、AI 理解和拟变更点。
- 不清除确认项，不修改正式 Plan，不增加 Version。
- Status 设为 `reconfirmation-pending`，只展示待提交 Replan 的理解摘要和拟变更点后停止。

当前消息没有可用于对应确认项的答复时，不写入任何内容，只展示待确认事项并要求用户在 `/arch-check replan <当前 Plan Ref>` 后补充答复。

理解不正确时继续 `/arch-check replan <当前 Plan Ref>` 修正待提交 Replan。只有精确的 `/arch-check confirm <当前 Plan Ref>` 才能提交；自然语言肯定不算 confirm。未经 confirm，不得进入 `/dev`。

## `/arch-check`

识别当前业务、产品、代码或架构与成熟方案的差距。Human Plan 只写问题价值、成熟方案启发、目标状态、关键取舍、范围边界和验收结果。设置 Version 为 1、Status 为 `draft`。

只展示短摘要、Status、Needs Reconfirmation、真实 Plan Ref 和下一步命令。若有待确认事项，下一步返回带真实 Plan Ref 的 `/arch-check replan`；否则同时说明可 `/dev <当前 Plan Ref>` 或继续 `/arch-check replan <当前 Plan Ref>`。随后停止。

## `/arch-check replan [Plan Ref]`

要求 Owner Skill 为 `arch-check`，Status 为 `draft` 或 `reconfirmation-pending`。

- Needs Reconfirmation 为空：按人类反馈调整方向或实施范围，增加 Version，Status 保持 `draft`。
- Needs Reconfirmation 非空：按 Reconfirmation 协议准备或修正待提交 Replan，Version 不变。

不重复完整外部调研。展示短摘要、真实 Plan Ref 和合法下一步后停止。

## `/arch-check confirm [Plan Ref]`

仅当当前消息精确为 `/arch-check confirm <当前 Plan Ref>` 时执行。要求 Owner Skill 为 `arch-check`、Status 为 `reconfirmation-pending`，并存在对应当前 Version 的待提交 Replan。

提交待提交 Replan，清除已解决的确认项，增加 Version 并记录变化。仍有未解决项时 Status 设为 `draft`，下一步继续 `/arch-check replan <新 Plan Ref>`；全部解决后 Status 设为 `draft`，下一步进入 `/dev <新 Plan Ref>`。confirm 只提交 Replan，不代表开发批准。
