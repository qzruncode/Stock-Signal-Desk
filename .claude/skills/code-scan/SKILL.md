---
name: code-scan
description: Use when the user invokes /code-scan or asks to find bugs, inefficiency, redundancy, maintainability problems, or other concrete code risks across the current project.
---

# Code Scan

All scan conclusions, issue descriptions, evidence, priorities, Human Plan content, replan content, and next-step guidance must be written in Simplified Chinese. Preserve code identifiers, file paths, API names, error messages, and quoted source text.

## Canonical Plan

Use one canonical Human Plan file as the source of truth.

- Create `docs/human-plans/HP-YYYYMMDD-HHMM-<topic>.md` on the first scan, regardless of length.
- Keep the same Plan ID when the selected findings move into `/dev`.
- Resolve an explicit Plan Ref first. Otherwise use the single unambiguous Plan Ref in the current conversation.
- If the referenced version differs from `Current Version`, stop and report a stale Plan Ref. Never silently switch versions.
- End every response with `Plan Ref: <path>#v<current-version>`.
- Never rewrite earlier versions or ledger entries. Replan by appending a version.
- Keep Original Request and Requirement Baseline unchanged unless the human explicitly confirms an append-only baseline amendment.
- Reviews bind to exact versions; new versions require fresh reviews.

The file must carry Plan ID, version/stage/status metadata, Original Request, Requirement Baseline, Baseline Amendments, Plan Versions, Review Ledger, Approval Ledger, Execution Ledger, and Audit Ledger. Each version must contain `Confirmed Decisions`, `Current Plan`, `Changes Since Previous Version`, `Unchanged Scope`, and `Needs Reconfirmation`.

If a legacy plan lacks this structure, add the metadata and ledgers without changing its content; preserve the existing plan as version 1.

## `/code-scan`

Scan the current project without editing code. Look across correctness, duplication, efficiency, structure, abstraction, async flow, state, cache, frontend performance, backend contracts, data consistency, security, reliability, and deployment risk.

Create version 1 with stage `code-scan` and status `draft`. Store the scan request verbatim. Prioritize the most serious evidence-backed issues and record affected files, impact, recommended direction, risk, and verification.

Return the Plan Ref. The human may use `/code-scan replan <Plan Ref>` to adjust priorities or `/dev <Plan Ref>` to continue the selected work in the same file.

## `/code-scan replan [Plan Ref]`

Read the canonical plan and human feedback. Append a new version in the same file.

Preserve the selected problem baseline. Record priority or scope changes explicitly, put unconfirmed changes under `Needs Reconfirmation`, and set status to `draft`. Do not implement fixes.
