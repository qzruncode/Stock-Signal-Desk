---
name: plan-check
description: Use when the user invokes /plan-check or asks for architectural and code-impact review of a Human Plan before implementation.
---

# Plan Check

All review conclusions, risks, evidence, required changes, reconfirmation items, and next-step guidance must be written in Simplified Chinese. Preserve code identifiers, file paths, API names, and quoted source text.

## Canonical Plan

Review one canonical Human Plan file. Never create a replacement plan.

- Resolve an explicit Plan Ref first. Otherwise use the single unambiguous Plan Ref in the current conversation.
- If the referenced version differs from `Current Version`, stop and report a stale Plan Ref. Never review a different version.
- Read the current version, Original Request, Requirement Baseline, Baseline Amendments, previous version, and existing Review Ledger.
- Inspect the existing code affected by the current version.
- Bind the result to the exact `Plan ID` and current version.
- Append the result to `Review Ledger`; do not edit Plan Versions, baseline, or earlier ledger entries.
- End the response with the same `Plan Ref`.

If a legacy plan lacks Plan ID and version metadata, add the canonical metadata and ledgers while preserving its existing content as version 1.

## `/plan-check [Plan Ref]`

Check whether the current version:

- preserves the original requirement, baseline amendments, and confirmed decisions
- explicitly accounts for changes from the previous version
- uses existing pages, components, hooks, services, APIs, utilities, state flows, and data structures
- integrates with current module boundaries, contracts, ownership, and dependency direction
- avoids duplicate logic, parallel systems, unnecessary abstractions, and unrelated rewrites
- covers user-visible behavior, edge cases, compatibility, and regression surface
- covers runtime, rendering, request, async, cache, storage, schema, query, migration, consistency, security, and reliability impact where relevant
- is specific enough to implement without guessing

Append a Review Ledger entry containing:

- reviewer: `/plan-check`
- reviewed version
- result: `pass` or `replan required`
- baseline fit
- existing-code and architecture fit
- system and behavior impact
- required replan changes
- items needing human reconfirmation

Set status to `replan-required` when the result fails. On pass, set status to `design-review-pending` when Frontend Impact is `yes` or `unknown`; otherwise set it to `ready-for-approval`.

If any required change alters the baseline, mark it for human reconfirmation instead of treating it as an ordinary implementation suggestion.

Do not rewrite the plan or edit production code. A `replan-required` result must be handled by the originating skill's `replan` command, which creates the next version in the same file.
