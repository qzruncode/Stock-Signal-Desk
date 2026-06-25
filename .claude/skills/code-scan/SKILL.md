---
name: code-scan
description: Use when the user invokes /code-scan or asks to find bugs, inefficiency, redundancy, maintainability problems, or other concrete code risks across the current project.
---

# Code Scan

所有产出使用简体中文。代码标识、路径、API 名称、错误信息和引用原文不翻译。

禁止修改项目源码和其他项目文件，只允许扫描代码并写入 `docs/human-plans/` 下的当前 Human Plan。

## `/code-scan`

扫描项目但不修改代码。内部可以全面检查，Human Plan 只保留最值得优先处理的一组问题。

不要输出完整问题库存。只写：

- 最严重的问题及证据
- 对用户、系统或维护成本的影响
- 推荐优先级
- 一个可进入 `/dev` 的连贯修复范围
- 验收结果

如果问题很多，选择最严重、最相关的一组，其余只做一句简短说明或暂不输出。

创建一个简洁 Plan 文件，只包含：Plan ID、Version、Owner Skill、Status、Frontend Impact、Requirement Baseline、Confirmed Decisions、Current Plan、Changes Since Last Plan、Unchanged Scope、Needs Reconfirmation、Review Status、Delivery Status、Revision Notes。

Frontend Impact 只使用 `yes`、`no` 或 `unknown`。

Owner Skill 设置为 `code-scan`，Status 设置为 `draft`。

凡命令引用已有 Plan，Plan Ref 固定为 `<Plan 文件路径>@v<Version>`；缺少版本或与文件中的当前 Version 不一致时停止处理。新建 Plan 不要求输入 Plan Ref。

写入后必须在聊天中直接展示简洁的 Requirement Baseline、Current Plan、Unchanged Scope、Needs Reconfirmation、Status 和 Plan Ref，然后停止。不得只显示预览入口或只说已生成。

每次停止前，根据当前 Plan 的 Owner Skill、Status、Needs Reconfirmation 和本技能流程说明下一步，并给出当前允许执行的完整命令。命令必须带入真实 Plan Ref，不留占位符；有多个合法选择时说明用途，无需继续时说明结束。只提示，不代用户执行下一步。

## `/code-scan replan [Plan Ref]`

根据人类反馈调整优先级和当前修复范围，增加 Version，只记录本轮变化。

只在用户明确调用 `/code-scan replan <当前 Plan Ref>` 时更新；普通自然语言反馈不得触发写入。要求 Owner Skill 为 `code-scan`、Status 为 `draft`。

保持 Owner Skill 为 `code-scan`、Status 为 `draft`。不扩写完整扫描报告，不修改代码。重新展示 Human Plan 后停止；确认后下一步只允许执行 `/dev <当前 Plan Ref>`，等待用户显式调用。
