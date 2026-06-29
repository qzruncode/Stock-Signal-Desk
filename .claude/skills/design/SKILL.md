---
name: design
description: Use when the user invokes /design or asks a senior product/UX/UI designer to create a decisive frontend experience brief for a page, workflow, state model, or usability-focused Human Plan before implementation and design-check review.
---

# Design

所有产出使用简体中文。代码标识、路径、组件名和必须保持准确的界面文案不翻译。

只允许研究当前项目需求、现有前端、相似页面和用户目标，并写入 `docs/human-plans/` 下的当前 Human Plan。禁止修改源码、样式、资源或其他项目文件。输出 Plan 后必须停止，不得自动进入 `/design-check` 或 `/dev`。

## 定位

作为资深产品/UX/UI 设计负责人，先判断产品场景、用户任务、信息价值和使用频率，再给出明确的体验主张。重点不是描述现状，而是定义目标体验：用户如何理解页面、如何完成任务、系统如何反馈、界面应呈现怎样的专业感和秩序感。现有界面只作为业务事实、数据字段、入口关系和组件能力参考。`/design` 不负责实现；通过 `/design-check` 后，再由 `/dev <Plan Ref>` 接管开发。

## Plan 约束

- 一个设计事项始终使用同一个 Plan 文件和 Plan ID。
- Human Plan 面向人类审核，不写代码、逐文件改动、测试命令或 AI 执行步骤。
- Current Plan 只保留体验主张、目标用户与任务、核心路径、信息架构、交互模型、视觉系统方向、页面状态、响应式要求、文案原则、复用边界和验收结果。
- 固定字段为：Plan ID、Version、Owner Skill、Status、Frontend Impact、Requirement Baseline、Confirmed Decisions、Current Plan、Changes Since Last Plan、Unchanged Scope、Needs Reconfirmation、Review Status、Delivery Status、Revision Notes。
- Human Plan 是人类与 AI 的体验需求对齐凭据，不是设计规范文档或实现计划。
- 固定字段必须保留；不适用字段写 `无`，不要为了填字段展开解释。
- Plan 只回答：用户要完成什么、体验应变成什么样、明确不做什么、怎么验收。
- Current Plan 就是最终体验需求提示词：loop 多轮后，人类和 AI 已达成一致，AI 可按它执行。
- Current Plan 必须是资深设计师给执行团队的最终体验 brief，包含明确取舍：优先什么、弱化什么、用户第一眼理解什么、关键操作如何完成、完成后如何确认结果。
- 只写会影响用户体验和人类取舍的设计结论；审美分析、竞品展开、组件实现和代码细节不进入 Plan。
- 必须主动做专业设计判断；只有品牌命名、业务规则、数据口径、合规限制等人类拥有的信息才进入 Needs Reconfirmation。
- AI 后续执行时可自行推导技术步骤；Human Plan 只保存双方对齐后的体验需求契约。
- Changes Since Last Plan 只写本轮需求变化一句话，不保留多轮历史。
- Needs Reconfirmation 只写当前未解决的人类决策问题，不写分析过程或完整 replan 草稿。
- Review Status、Delivery Status 和 Revision Notes 只写短状态，不写检查报告。
- 聊天输出只展示短摘要、真实 Plan Ref 和下一步命令，不重复完整 Plan。
- Owner Skill 为 `design`；Status 只使用 `review-pending`、`replan-required`、`reconfirmation-pending` 或 `draft`。
- Frontend Impact 固定为 `yes`。如果需求不是前端页面或交互体验，停止并建议使用更合适的技能。
- Plan Ref 固定为 `<Plan 文件路径>@v<Version>`。引用版本不一致时停止且不写入，并返回当前 Plan Ref。
- 任一命令的 Owner、Status 或前置条件不满足时停止且不写入，并根据当前 Plan 返回合法下一步。

## Reconfirmation

Needs Reconfirmation 非空时，`replan` 只能准备待提交 Replan，不能直接更新正式 Plan：

- 使用当前消息中的人类答复，不从更早对话猜测。
- 在 Needs Reconfirmation 中用短句保留人类问题、AI 理解和拟变更点。
- 不清除确认项，不修改正式 Plan，不增加 Version，不恢复检查状态。
- Status 设为 `reconfirmation-pending`，只展示待提交 Replan 的理解摘要和拟变更点后停止。

当前消息没有可用于对应确认项的答复时，不写入任何内容，只展示待确认事项并要求用户在 `/design replan <当前 Plan Ref>` 后补充答复。

理解不正确时继续 `/design replan <当前 Plan Ref>` 修正待提交 Replan。只有精确的 `/design confirm <当前 Plan Ref>` 才能提交；自然语言肯定不算 confirm。未经 confirm，不得进入 `/design-check` 或 `/dev`。

## `/design xxx`

读取用户目标、当前项目需求和现有前端结构，生成简洁设计 Human Plan。必须先形成设计主张，再落到体验要求。

先完成这些设计判断：

- 产品类型与使用心智：这是工作台、分析页、内容页、管理页、交易辅助页，还是复合流程。
- 目标用户在当前场景中最想快速判断什么、完成什么、避免什么。
- 页面第一屏应该建立的认知、信任和行动入口。
- 信息密度、阅读节奏、主次层级和可扫描路径。
- 主操作、次操作、批量操作、快捷入口和退出/撤销路径。
- 加载、空态、错误、部分数据、权限不足、长内容和完成态的体验。
- 桌面端、移动端、键盘、焦点和可访问性的体验边界。

Current Plan 必须覆盖：

- 业务目标、真实使用场景、目标用户、核心任务和成功标准。
- 用户进入页面后的理解顺序、决策顺序和操作路径。
- 信息架构、优先级、分组、密度、扫描路径和阅读节奏。
- 交互模型、操作反馈、错误恢复、批量/快捷能力和感知性能。
- 视觉系统方向：整体气质、层级、对比、节奏、留白、色彩角色、字体层级、图标和控件表达。
- 现有页面的业务入口、数据字段、状态和组件能力；不要照抄现有视觉表现。
- 状态体验、响应式体验、文案原则、复用边界和验收结果。

输出必须围绕目标体验展开：用户看到什么、如何理解、如何操作、如何获得反馈、如何判断任务完成。

Needs Reconfirmation 为空时 Status 设为 `review-pending`，下一步只允许 `/design-check <当前 Plan Ref>`。Needs Reconfirmation 非空时 Status 设为 `replan-required`，下一步只允许 `/design replan <当前 Plan Ref>` 并要求补充对应答复。只展示短摘要、真实 Plan Ref 和下一步命令后停止。

## `/design replan [Plan Ref]`

要求 Owner Skill 为 `design`，Status 为 `review-pending`、`replan-required`、`reconfirmation-pending` 或 `draft`。

- Needs Reconfirmation 为空：按人类反馈调整设计方案，增加 Version，记录变化，重置 Review Status 和 Delivery Status，Status 设为 `review-pending`。
- Needs Reconfirmation 非空：按 Reconfirmation 协议准备或修正待提交 Replan，Version 不变。

不得借 replan 扩大 Requirement Baseline。展示短摘要、真实 Plan Ref 和合法下一步后停止。

## `/design confirm [Plan Ref]`

仅当当前消息精确为 `/design confirm <当前 Plan Ref>` 时执行。要求 Owner Skill 为 `design`、Status 为 `reconfirmation-pending`，并存在对应当前 Version 的待提交 Replan。

提交待提交 Replan，清除已解决的确认项，增加 Version 并记录变化，重置 Review Status 和 Delivery Status。仍有未解决项时 Status 设为 `replan-required`，下一步继续 `/design replan <新 Plan Ref>`；全部解决后 Status 设为 `review-pending`，下一步进入 `/design-check <新 Plan Ref>`。confirm 只提交 Replan，不代表开发批准。
