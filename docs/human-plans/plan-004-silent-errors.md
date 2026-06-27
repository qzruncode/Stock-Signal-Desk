---
name: 消除后端静默吞错并补充日志
description: 将后端端点中静默吞掉异常的位置改为捕获具体类型并补充日志,提高可观测性
type: refactor
---

# Plan 004 · 消除后端静默吞错并补充日志

## 固定字段

- Plan ID: plan-004-silent-errors
- Version: 3
- Owner Skill: audit
- Status: complete
- Frontend Impact: no
- Requirement Baseline: 用户期望异常路径不再被静默吞掉;真实错误可见、可定位;正常业务返回值不变
- Confirmed Decisions: v2 的 17 处修改已全部实现,审计后保留;仅修正日志级别
- Current Plan: v2 的 17 处静默吞错修改已全部就位;当前返工仅修正 3 处日志级别:
  - `data_provider/akshare_fetcher.py` 中 `eastmoney_patch` 失败的日志级别从 `debug` 提升至 `warning`
  - `src/services/market_theme_service.py` 中 JSON 修复失败的日志级别从 `warning` 提升至 `error`
  - `src/services/industry_cycle_service.py` 中 JSON 提取和修复失败的日志级别从 `warning` 提升至 `error`
- Changes Since Last Plan: v3 为 audit 返工,仅修正 3 处日志级别,不改变任何其他行为
- Unchanged Scope: 不修改正常业务返回值;不修改正常路径的异常处理;不重构既有日志格式;不修改端点 URL、请求体或响应体形状;不引入新的日志后端;不改变已带日志的 except 块;不修改 v2 验收通过的 17 处修改中的其他 14 处
- Needs Reconfirmation: 无
- Review Status: v3 plan-check 通过; design-check 不适用
- Delivery Status: v2 approved and implemented; v3 审计返工已批准并实现; 3/3 日志级别修正完成, 编译通过; v3 audit 通过, 流程结束
- Revision Notes: v1 batch-code-scan 草稿;v2 dev 实现 17 处修改;v3 audit 返工修正 3 处日志级别

## 为什么修

多个端点存在 `except Exception: return None` 或 `except: pass` 的静默吞错模式。这些位置把真实异常吃掉,只返回 None 或默认值,导致上游调用方无法区分「数据不存在」与「下游故障」,出问题排查时极难定位。

## 目标结果

完成后应满足:

- 后端 Python 代码中 17 处静默吞错全部消除
- 每处按场景分类处理:数据源 fetcher/缓存/字段解析/服务层/映射回退,各使用合适的日志级别与异常类型
- 日志中包含关键上下文:函数名、异常类型与异常消息
- 正常业务返回值不变;异常路径返回值与原行为保持兼容
- 代码编译通过,既有的测试全绿

## 明确不做什么

- 不修改正常业务返回值与既有正常路径
- 不修改正常路径的异常处理(只针对 17 处静默吞错点)
- 不重构既有日志格式或日志级别
- 不修改端点 URL、请求体或响应体形状
- 不引入新的日志后端
- 不改变已带有日志的 except 块(如已有 `logger.exception` / `logger.warning` 的位置)

## 验收

- 所有 17 处静默吞错消失;新 grep 不出 `except Exception:\s+return None` 或 `except Exception:\s+pass` 等静默吞错模式
- 每处按场景使用正确的日志级别(warning 或 error)
- 异常路径有清晰的日志输出,包含函数名与异常类型
- 正常业务返回值不变;既有测试全绿
- 日志格式与既有风格一致,不引入新的日志后端