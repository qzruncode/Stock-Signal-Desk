---
name: arch-check
description: Use when the user invokes /arch-check or asks whether current business logic, product behavior, code, or architecture is reinventing wheels or diverging from mature solutions.
---

# Arch Check

所有产出使用简体中文。代码标识、路径、API 名称、仓库名称、链接和引用原文不翻译。

禁止修改项目源码和其他项目文件，只允许研究现有代码并写入 `docs/human-plans/` 下的当前 Human Plan。

## `/arch-check`

先检查本地代码，再使用可用的网络或 GitHub MCP 查找成熟方案。内部可以广泛研究，Human Plan 只保留最终有用的判断。

不要输出调研流水账或大量候选方案。只写：

- 当前项目的具体问题和本地证据
- 最相关的成熟方案或参考
- 为什么适合当前项目
- 推荐方向和关键取舍
- 一个可进入 `/dev` 的实施范围
- 风险和验收结果

外部参考保留必要链接，但不复制大段资料。

创建一个简洁 Plan 文件，只包含：Plan ID、Version、Owner Skill、Status、Frontend Impact、Requirement Baseline、Confirmed Decisions、Current Plan、Changes Since Last Plan、Unchanged Scope、Needs Reconfirmation、Review Status、Delivery Status、Revision Notes。

Frontend Impact 只使用 `yes`、`no` 或 `unknown`。

Owner Skill 设置为 `arch-check`，Status 设置为 `draft`。

凡命令引用已有 Plan，Plan Ref 固定为 `<Plan 文件路径>@v<Version>`；缺少版本或与文件中的当前 Version 不一致时停止处理。新建 Plan 不要求输入 Plan Ref。

写入后必须在聊天中直接展示简洁的 Requirement Baseline、Current Plan、关键取舍、Unchanged Scope、Needs Reconfirmation、Status 和 Plan Ref，然后停止。不得只显示预览入口或只说已生成。

每次停止前，根据当前 Plan 的 Owner Skill、Status、Needs Reconfirmation 和本技能流程说明下一步，并给出当前允许执行的完整命令。命令必须带入真实 Plan Ref，不留占位符；有多个合法选择时说明用途，无需继续时说明结束。只提示，不代用户执行下一步。

## `/arch-check replan [Plan Ref]`

根据人类反馈更新推荐方向和实施范围，增加 Version，只记录本轮变化。

只在用户明确调用 `/arch-check replan <当前 Plan Ref>` 时更新；普通自然语言反馈不得触发写入。要求 Owner Skill 为 `arch-check`、Status 为 `draft`。

保持 Owner Skill 为 `arch-check`、Status 为 `draft`。不重复外部调研，不修改代码。重新展示 Human Plan 后停止；确认后下一步只允许执行 `/dev <当前 Plan Ref>`，等待用户显式调用。
