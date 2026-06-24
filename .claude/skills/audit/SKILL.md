---
name: audit
description: Use when the user invokes /audit or asks to review completed AI-written changes against the approved requirement, existing codebase, and engineering quality.
---

# Audit

All audit conclusions, findings, evidence, fix plans, replan content, verification results, and next-step guidance must be written in Simplified Chinese. Preserve code identifiers, file paths, API names, error messages, and quoted source text.

## Canonical Plan

Audit the same canonical Human Plan used for approval and implementation. Never create a separate audit plan.

- Resolve an explicit Plan Ref first. Otherwise use the single unambiguous Plan Ref in the current conversation.
- If the referenced version differs from `Current Version`, stop and report a stale Plan Ref. Never silently switch versions.
- Read Original Request, Requirement Baseline, all amendments, the approved version, approval entry, and execution entry.
- Bind audit findings to the exact Plan ID and implemented version.
- Append findings to `Audit Ledger`; do not rewrite prior versions or ledgers.
- If fixes are needed, `/audit replan` appends the next version in the same file.
- End every response with the same Plan Ref.

If a legacy plan lacks Plan ID and version metadata, add the canonical metadata and ledgers while preserving its existing content as version 1.

## `/audit [Plan Ref]`

Before auditing, require status `implemented` and confirm that `Approval Ledger` and `Execution Ledger` both refer to the current Plan ID and current version. If they do not, stop.

Inspect actual code changes and relevant existing code. Check:

- baseline and approved-plan compliance
- reuse of existing components, hooks, services, utilities, contracts, and patterns
- layering, ownership, dependency direction, abstraction, duplication, and maintainability
- behavior, edge cases, compatibility, and regression risk
- frontend rendering, state, data fetching, responsiveness, and style consistency
- backend contracts, validation, errors, async flow, and external dependencies
- schema, query, consistency, idempotency, cache, storage, and stale-data behavior
- authentication, input safety, secrets, retries, timeouts, fallback, and availability
- verification completeness

Append an Audit Ledger entry with result `pass` or `fixes required`, findings, evidence, and missing verification.

If it passes, set status to `complete`. If fixes are required, set status to `audit-fixes-required`, keep the same Plan ID, and suggest `/audit replan <Plan Ref>`.

## `/audit replan [Plan Ref]`

Append a new version with stage `audit-fix` and status `review-pending`. Preserve the original baseline and decisions. Convert accepted audit findings into a focused fix plan, and put any scope or behavior change under `Needs Reconfirmation`.

The new version must contain `Confirmed Decisions`, `Current Plan`, `Changes Since Previous Version`, `Unchanged Scope`, and `Needs Reconfirmation`.

The new version must repeat `/plan-check` and, when relevant, `/design-check`.

## `/audit approve [Plan Ref]`

Implement only the current audit-fix version.

Require status `ready-for-approval`, an empty `Needs Reconfirmation`, a passing `/plan-check` for the exact version, and a passing `/design-check` when frontend behavior is involved.

Append approval, set status to `approved`, implement, append execution results, and set status to `implemented`. Do not create a new plan. Finish by running `/audit <Plan Ref>` again until the Audit Ledger records `pass`.
