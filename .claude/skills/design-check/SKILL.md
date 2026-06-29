---
name: design-check
description: Use when the user invokes /design-check or a proposed frontend change needs senior UX/UI review for product fit, task flow, interaction model, visual system, responsive behavior, and usability before implementation.
---

# Design Check

所有产出使用简体中文。代码标识、路径、组件名和必须保持准确的界面文案不翻译。

只允许读取项目，并在当前 Plan 中更新 Frontend Impact、Review Status、Status 和确实需要人类决策的 Needs Reconfirmation。禁止修改其他字段或项目文件。

Owner Skill 决定后续命令前缀：`design` 对应 `/design`，`dev` 对应 `/dev`，`bug-fix` 对应 `/bug-fix`，`audit` 对应 `/audit`。

## `/design-check [Plan Ref]`

Plan Ref 必须是 `<Plan 文件路径>@v<Version>` 且匹配当前 Version，否则停止且不写入。

按以下顺序检查前置状态：

1. Owner Skill 必须是 `design`、`dev`、`bug-fix` 或 `audit`。
2. Status 为 `reconfirmation-pending` 时停止检查：理解正确返回对应 Owner 的 `confirm`，理解不正确返回对应 Owner 的 `replan`。
3. Needs Reconfirmation 非空时停止检查，返回对应 Owner 的 `replan`。
4. Status 必须是 `review-pending`；Owner 为 `dev`、`bug-fix` 或 `audit` 时还要求当前 Version 的 plan-check 已通过；否则返回当前合法下一步。

检查当前版本是否覆盖：

- 体验目标、目标用户、核心任务和成功标准。
- 产品类型、使用心智和设计主张是否清楚。
- 设计取舍是否明确：优先什么、弱化什么、第一眼理解什么、关键操作如何完成。
- 信息架构、页面层级、核心用户路径和关键决策点。
- 是否保留必要的业务入口、数据字段和状态，不把现有视觉表现当成约束。
- 是否形成完整体验方案：目标、路径、结构、交互、视觉、状态和验收完整。
- 视觉系统方向、层级、密度、节奏、对比、控件表达和品牌感。
- 交互模型、操作效率、状态反馈、错误恢复、快捷能力和感知性能。
- loading、empty、error、disabled、success 和部分数据状态。
- 桌面端与移动端响应式行为。
- 可访问性、键盘和焦点行为。
- 一次性 CSS、脆弱布局、重复组件和视觉混乱风险。
- 站在体验需求层检查：人类是否能清楚知道 AI 准备交付什么体验、什么不交付、如何验收。
- 检查 Current Plan 是否就是最终体验需求提示词；若主体偏离体验需求层，要求对应 Owner replan。
- 如果 Needs Reconfirmation 包含可由设计师根据产品目标判断的体验选择，要求对应 Owner replan 直接作出设计决策；只保留人类掌握的业务、数据、合规或品牌决策。
- 检查 Changes Since Last Plan、Needs Reconfirmation、Review Status 是否短状态；若它们像历史记录或检查报告，要求对应 Owner replan 压缩。
- 保持一屏可审；如果 Plan 像设计规范全文、审美分析或长清单，要求对应 Owner replan 压缩。

输出仅包含结论、阻塞体验问题、必须修改的设计要求和需要人类决策的事项。Frontend Impact 为 `unknown` 时先判断为 `yes` 或 `no`；确认无前端影响时可判定不适用。

## 结果

- 需要 AI 调整且无需人类决策：记录当前 Version 结果，把 Status 设为 `replan-required`，返回对应 Owner 的 `replan`。
- 需要人类决策：把明确决策点写入 Needs Reconfirmation，不得替人作答或只写在聊天中；Status 设为 `replan-required`，返回对应 Owner 的 `replan`，后续必须经过该 Owner 的 Reconfirmation。
- Owner 为 `design` 且通过：记录当前 Version 结果，Status 设为 `draft`，返回 `/dev <当前 Plan Ref>`。
- Owner 为 `dev`、`bug-fix` 或 `audit` 且通过或不适用：记录当前 Version 结果，Status 设为 `ready-for-approval`，返回对应 Owner 的 `approve`。

展示结论、真实 Plan Ref 和完整下一步命令后停止，不得自动调用下一技能。
