---
name: design-check
description: Use when the user invokes /design-check or a proposed frontend change needs visual, interaction, consistency, responsive, or usability review before implementation.
---

# Design Check

所有产出使用简体中文。代码标识、路径、组件名和必须保持准确的界面文案不翻译。

## `/design-check [Plan Ref]`

必须先通过当前版本 `/plan-check`。深入检查现有页面和设计体系，但只输出阻塞实施的设计问题。

如果 Plan Ref 的版本不是当前 Version，停止处理。Needs Reconfirmation 不为空时不得判定通过。

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

通过时不要复述完整 Plan。无前端影响时标记 `不适用`。

结果覆盖写入当前 Plan 的 Review Status，不创建新 Plan，不追加长篇检查记录，不直接修改 Current Plan。

需要调整时返回当前阶段对应的 replan；通过或不适用时将 Status 设为 `ready-for-approval`。
