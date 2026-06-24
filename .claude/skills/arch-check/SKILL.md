---
name: arch-check
description: Use when the user invokes /arch-check or asks whether current business logic, product behavior, code, or architecture is reinventing wheels or diverging from mature solutions.
---

# Arch Check

All local analysis, external-solution comparisons, recommendations, Human Plan content, replan content, and next-step guidance must be written in Simplified Chinese. Preserve code identifiers, file paths, API names, repository names, links, and quoted source text.

## Canonical Plan

Use one canonical Human Plan file as the source of truth.

- Create `docs/human-plans/HP-YYYYMMDD-HHMM-<topic>.md` on the first check, regardless of length.
- Keep the same Plan ID when the recommendation moves into `/dev`.
- Resolve an explicit Plan Ref first. Otherwise use the single unambiguous Plan Ref in the current conversation.
- If the referenced version differs from `Current Version`, stop and report a stale Plan Ref. Never silently switch versions.
- End every response with `Plan Ref: <path>#v<current-version>`.
- Never rewrite earlier versions or ledger entries. Replan by appending a version.
- Keep Original Request and Requirement Baseline unchanged unless the human explicitly confirms an append-only baseline amendment.
- Reviews bind to exact versions; new versions require fresh reviews.

The file must carry Plan ID, version/stage/status metadata, Original Request, Requirement Baseline, Baseline Amendments, Plan Versions, Review Ledger, Approval Ledger, Execution Ledger, and Audit Ledger. Each version must contain `Confirmed Decisions`, `Current Plan`, `Changes Since Previous Version`, `Unchanged Scope`, and `Needs Reconfirmation`.

If a legacy plan lacks this structure, add the metadata and ledgers without changing its content; preserve the existing plan as version 1.

## `/arch-check`

Inspect local code first, then use available network or GitHub MCP tools to compare the relevant business workflow, product behavior, code, framework usage, data flow, architecture, reliability, and security design with mature solutions.

Create version 1 with stage `arch-check` and status `draft`. Store the check request verbatim. Record local evidence, relevant external references, the recommended direction, how it translates into this project, migration risk, and verification.

Do not edit code. Return the Plan Ref. The human may use `/arch-check replan <Plan Ref>` or `/dev <Plan Ref>` so implementation continues in the same file.

## `/arch-check replan [Plan Ref]`

Read the canonical plan and human feedback. Append a new version in the same file.

Preserve the accepted problem and reference direction. Record recommendation or migration-scope changes explicitly, put unconfirmed changes under `Needs Reconfirmation`, and set status to `draft`. Do not implement changes.
