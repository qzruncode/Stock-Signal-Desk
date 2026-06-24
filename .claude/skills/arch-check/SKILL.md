---
name: arch-check
description: Use when the user invokes /arch-check or asks whether current business logic, product behavior, code, or architecture is reinventing wheels or diverging from mature solutions.
---

# Arch Check

所有产出使用简体中文。代码标识、路径、API 名称、仓库名称、链接和引用原文不翻译。

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

创建一个简洁 Plan 文件，只包含：Plan ID、Version、Status、Frontend Impact、Requirement Baseline、Confirmed Decisions、Current Plan、Changes Since Last Plan、Unchanged Scope、Needs Reconfirmation、Review Status、Delivery Status、Revision Notes。

聊天中只返回摘要、Plan Ref 和下一步。

## `/arch-check replan [Plan Ref]`

根据人类反馈更新推荐方向和实施范围，增加 Version，只记录本轮变化。

如果 Plan Ref 的版本不是当前 Version，停止处理。不重复外部调研，不修改代码。确认后进入 `/dev <Plan Ref>`。
