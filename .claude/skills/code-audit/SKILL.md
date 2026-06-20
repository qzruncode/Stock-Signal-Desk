---
name: code-audit
description: Project-local code audit. Use when the user asks to scan, audit, govern, review, or improve the whole project's engineering quality and wants a prioritized modification list rather than immediate code changes.
---

# Code Audit

Scan the project as a senior full-stack engineer and output a prioritized code governance modification list.

This skill audits first. It does not modify code during the audit.

## Scan Domains

- Architecture: layering, module boundaries, dependency direction, frontend/backend responsibility split.
- Maintainability: file size, function complexity, naming, dead code, duplication, coupling, unclear abstractions.
- Frontend quality: component boundaries, state ownership, data fetching, render performance, responsive behavior, accessibility, bundle risk.
- Backend quality: API contracts, validation, error handling, service orchestration, async/task behavior, external dependency handling.
- Data and storage: schema, query patterns, indexes, migrations, cache/storage keys, consistency, idempotency.
- Performance: page load, re-render waste, request volume, payload size, backend latency, algorithm complexity, batching, caching.
- Security: input validation, auth boundaries, secret handling, injection risks, unsafe file/network access, CORS/host rules, dependency risk.
- Reliability: retry/fallback, timeout handling, degraded states, concurrency, task recovery, observability, error visibility.
- Product correctness: code behavior versus user-facing requirements, page docs, business decisions, edge cases, expected workflows.
- Delivery risk: test gaps, build risk, deployment risk, environment assumptions, fragile scripts, config drift.

Scan across domains, but report only issues with concrete code evidence.

## Output

```md
# 代码治理修改清单

## 总体治理判断
- 当前最大风险：
- 最值得先治理的方向：
- 不建议马上动的方向：

## 优先级清单
| 优先级 | 类型 | 问题 | 位置 | 建议动作 |
|---|---|---|---|---|

## 详细问题
### P0/P1/P2 - 问题标题
- 类型：
- 位置：
- 证据：
- 影响：
- 建议改法：
- 预期收益：
```

Priority meaning:

- P0: stability, security, data correctness, or core workflow risk.
- P1: structure, performance, reuse, maintainability, or delivery risk worth fixing soon.
- P2: useful long-term cleanup that does not block current delivery.
