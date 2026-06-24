---
name: design-check
description: Use when the user invokes /design-check or a proposed frontend change needs visual, interaction, consistency, responsive, or usability review before implementation.
---

# Design Check

## Canonical Plan

Review the same canonical Human Plan already used by the workflow. Never create a design plan.

- Resolve an explicit Plan Ref first. Otherwise use the single unambiguous Plan Ref in the current conversation.
- If the referenced version differs from `Current Version`, stop and report a stale Plan Ref. Never review a different version.
- Require a passing `/plan-check` entry for the current version before reviewing design.
- Read the current version, Requirement Baseline, previous reviews, and existing frontend code.
- Bind the result to the exact Plan ID and current version.
- Append the result to `Review Ledger`; do not edit Plan Versions or earlier entries.
- End the response with the same Plan Ref.

If a legacy plan lacks Plan ID and version metadata, add the canonical metadata and ledgers while preserving its existing content as version 1.

## `/design-check [Plan Ref]`

If there is no frontend or user-interaction impact, append a `not applicable` result for the current version.

Otherwise inspect the affected pages, components, styles, layout, and interaction patterns. Check:

- visual consistency, hierarchy, spacing, density, typography, color, and control patterns
- clarity and efficiency of the user path
- information priority and scanability
- loading, empty, error, disabled, success, long-running, and partial-data states
- desktop and mobile behavior
- component reuse and consistency with similar features
- accessibility and focus behavior
- risk of one-off CSS, fragile layout, nested cards, awkward spacing, or inconsistent controls

Append a Review Ledger entry containing:

- reviewer: `/design-check`
- reviewed version
- result: `pass`, `replan required`, or `not applicable`
- existing-UI fit
- interaction and visual concerns
- required replan changes
- items needing human reconfirmation

Set status to `replan required` when the result fails. Set it to `ready for approval` on `pass` or `not applicable`.

Do not rewrite the plan or edit production code. A `replan required` result returns to the originating skill's `replan` command, which appends the next version to the same file.
