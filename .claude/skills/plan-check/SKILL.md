---
name: plan-check
description: Project-local Human Plan review. Use when the user invokes /plan-check or asks to review a Human Plan before implementation.
---

# Plan Check

## `/plan-check`

Review the current Human Plan as an architect before implementation.

Before judging the plan, inspect the existing code that the plan would touch. The review must compare the plan against current project structure, not only against the written requirement.

Check:

- existing foundation: whether the project already has pages, components, hooks, services, APIs, utilities, state flows, or data structures that should be reused
- integration fit: whether the new logic fits into existing module boundaries, naming, contracts, state ownership, and data flow
- duplication risk: whether the plan creates parallel logic instead of extending or reusing existing code
- abstraction fit: whether shared behavior belongs in an existing abstraction or needs a small new one
- system impact: affected modules, pages, APIs, jobs, data flow, state, cache, and storage
- architecture fit: layering, module boundaries, dependency direction, abstraction ownership
- code impact: reuse, duplication, component/service split, complexity, maintainability
- behavior impact: user-visible changes, edge cases, backward compatibility, regression surface
- runtime impact: performance, request volume, rendering cost, async behavior, failure handling
- data impact: schema, query, migration, idempotency, consistency, stale data risk
- safety impact: validation, auth boundary, secrets, injection, unsafe file/network access
- execution risk: whether the plan can be implemented without guessing or broad unrelated rewrites

Output:

- conclusion: 可执行 / 需要重写 Human Plan
- existing code fit
- architecture concerns
- code impact concerns
- concrete replan suggestions

If the plan ignores existing code that should be reused, mark it as `需要重写 Human Plan` and say exactly which existing structure the next plan should build on.
