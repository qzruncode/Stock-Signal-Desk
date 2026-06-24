---
name: code-scan
description: Use when the user invokes /code-scan or asks to find bugs, inefficiency, redundancy, maintainability problems, or other concrete code risks across the current project.
---

# Code Scan

所有产出使用简体中文。代码标识、路径、API 名称、错误信息和引用原文不翻译。

禁止修改项目源码，只允许扫描代码并创建或更新 Human Plan。

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

Owner Skill 设置为 `code-scan`，Status 设置为 `draft`。

聊天中只返回摘要、Plan Ref 和下一步。

## `/code-scan replan [Plan Ref]`

根据人类反馈调整优先级和当前修复范围，增加 Version，只记录本轮变化。

如果 Plan Ref 的版本不是当前 Version，停止处理。保持 Owner Skill 为 `code-scan`、Status 为 `draft`。不扩写完整扫描报告，不修改代码。确认后进入 `/dev <Plan Ref>`。
