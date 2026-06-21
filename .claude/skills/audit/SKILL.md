---
name: audit
description: Project-local post-implementation code audit. Use when the user invokes /audit to review AI-written code and produce a human-readable fix plan before approved follow-up changes.
---

# Audit

## `/audit`

Review the code written by AI after implementation.

Focus on whether the change actually fits the approved Human Plan and the existing codebase.

Check:

- plan match: implemented behavior matches the approved Human Plan
- existing code fit: code reuses existing components, hooks, services, utilities, contracts, and patterns where appropriate
- architecture fit: layering, module boundaries, dependency direction, abstraction ownership
- code quality: duplication, complexity, naming, file size, component/service split, maintainability
- behavior correctness: user-visible behavior, edge cases, backward compatibility, regression surface
- frontend quality: render cost, state ownership, data fetching, responsive behavior, style consistency
- backend quality: API contract, validation, error handling, async/task flow, external dependency handling
- data safety: schema/query impact, consistency, idempotency, stale data, cache/storage behavior
- security and reliability: auth boundary, input safety, secrets, retries, timeouts, fallback behavior
- verification: whether the right checks were run and whether gaps remain

Output:

- conclusion: 通过 / 需要返工
- main risks
- code issues
- missing verification
- Human Plan for required fixes, if follow-up changes are needed

After outputting the audit result and fix Human Plan, wait for feedback or `/audit approve`.

## `/audit approve`

Implement the approved audit fix plan.

Apply only the approved follow-up fixes, verify them, and finish with a short summary of changed files, verification result, and remaining risk.

## `/audit replan`

Rewrite the audit fix Human Plan using the user's feedback.

After outputting the revised Human Plan, wait for feedback or `/audit approve`.
