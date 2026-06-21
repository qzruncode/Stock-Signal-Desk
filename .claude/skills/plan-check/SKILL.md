---
name: plan-check
description: Project-local Human Plan review. Use when the user invokes /plan check or asks to review a Human Plan before implementation.
---

# Plan Check

## `/plan check`

Review the current Human Plan as an architect before implementation.

Check:

- system impact: affected modules, pages, APIs, jobs, data flow, state, cache, and storage
- architecture fit: layering, module boundaries, dependency direction, abstraction ownership
- code impact: reuse, duplication, component/service split, complexity, maintainability
- behavior impact: user-visible changes, edge cases, backward compatibility, regression surface
- runtime impact: performance, request volume, rendering cost, async behavior, failure handling
- data impact: schema, query, migration, idempotency, consistency, stale data risk
- safety impact: validation, auth boundary, secrets, injection, unsafe file/network access
- execution risk: whether the plan can be implemented without guessing or broad unrelated rewrites

Output:

- Pass / Needs replan
- architecture concerns
- code impact concerns
- concrete replan suggestions

If the plan needs changes, suggest what the next Human Plan should adjust.
