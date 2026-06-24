---
name: design-check
description: Use when the user invokes /design-check or a proposed frontend change needs visual, interaction, consistency, responsive, or usability review before implementation.
---

# Design Check

所有产出使用简体中文。代码标识、路径、组件名和必须保持准确的界面文案不翻译。

禁止修改项目源码和其他项目文件，只允许读取代码，并在 `docs/human-plans/` 下的当前 Plan 中更新 Frontend Impact、Review Status 与 Status。

## `/design-check [Plan Ref]`

必须先确认 Review Status 中当前 Version 的 `/plan-check` 已通过。深入检查现有页面和设计体系，但只输出阻塞实施的设计问题。

Plan Ref 固定为 `<Plan 文件路径>@v<Version>`。缺少版本或与文件中的当前 Version 不一致时停止处理。Needs Reconfirmation 不为空时不得判定通过。

Owner Skill 必须是 `dev`、`bug-fix` 或 `audit`，Status 必须是 `review-pending`；否则停止检查。

检查：

- 页面信息层级和核心用户路径
- 与现有组件、样式和相似页面的一致性
- 交互效率、状态反馈和可理解性
- loading、empty、error、disabled、success 和部分数据状态
- 桌面端与移动端响应式行为
- 可访问性、键盘和焦点行为
- 一次性 CSS、脆弱布局、重复组件和视觉混乱风险

输出只包含：

- 结论：`通过`、`需要 replan` 或 `不适用`
- 阻塞体验的关键问题
- Current Plan 必须补充或修改的设计要求
- 需要人类重新确认的事项

通过时不要复述完整 Plan。Frontend Impact 为 `unknown` 时先判断并更新为 `yes` 或 `no`；只有确认无前端影响时才能标记 `不适用`。

结果覆盖写入当前 Plan 的 Review Status，并标明当前 Version；不创建新 Plan，不追加长篇检查记录，不修改 Requirement Baseline、Confirmed Decisions、Current Plan、Unchanged Scope 或 Delivery Status。

需要调整时将 Status 设为 `replan-required`，并根据 Owner Skill 返回 `/dev replan <当前 Plan Ref>`、`/bug-fix replan <当前 Plan Ref>` 或 `/audit replan <当前 Plan Ref>`。

通过或不适用时将 Status 设为 `ready-for-approval`，并按 Owner Skill 返回 `/dev approve <当前 Plan Ref>`、`/bug-fix approve <当前 Plan Ref>` 或 `/audit approve <当前 Plan Ref>`。

更新 Plan、展示结论、当前 Plan Ref 和唯一下一步命令后停止。不得自动调用下一技能。
