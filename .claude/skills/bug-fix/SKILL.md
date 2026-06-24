---
name: bug-fix
description: Use when the user invokes /bug after a concrete bug and its affected behavior have already been identified.
---

# Bug Fix

## Canonical Plan

Use one canonical Human Plan file as the source of truth for the whole workflow.

- Create `docs/human-plans/HP-YYYYMMDD-HHMM-<topic>.md` only when no Plan Ref exists.
- When a Plan Ref exists, continue that exact file and Plan ID.
- Resolve the plan from an explicit path first. Otherwise use the single unambiguous Plan Ref in the current conversation. Never guess between multiple plans.
- If the referenced version differs from `Current Version`, stop and report a stale Plan Ref. Never silently switch versions.
- End every response with `Plan Ref: <path>#v<current-version>`.
- Never rewrite earlier versions or ledger entries. Replan by appending a new version.
- Keep Original Request and Requirement Baseline unchanged unless the human explicitly confirms a baseline amendment.
- Reviews and approvals bind to an exact version. A new version invalidates earlier results.

The file must carry Plan ID, version/stage/status metadata, Original Request, Requirement Baseline, Baseline Amendments, Plan Versions, Review Ledger, Approval Ledger, Execution Ledger, and Audit Ledger. Each version must contain `Confirmed Decisions`, `Current Plan`, `Changes Since Previous Version`, `Unchanged Scope`, and `Needs Reconfirmation`.

If a legacy plan lacks this structure, add the metadata and ledgers without changing its content; preserve the existing plan as version 1.

## `/bug xxx`

The bug is already located. Read supplied diagnosis, evidence, and any Plan Ref.

Create version 1 with stage `bug-fix`, or append a bug-fix version to the referenced plan. Set status to `review pending`. Store a new original request verbatim, or preserve the existing one. Record expected behavior, broken behavior, established root cause and evidence, affected existing code, fix direction, regression surface, and verification approach.

Do not edit code. Return the Plan Ref and require `/plan-check <Plan Ref>`, followed by `/design-check <Plan Ref>` when frontend behavior is affected.

## `/bug replan [Plan Ref]`

Read the canonical plan and all current-version reviews. Append a new version in the same file.

Keep the fix limited to the identified bug. Preserve expected behavior and confirmed scope. Put behavior changes or unrelated cleanup under `Needs Reconfirmation`. Set status to `review pending`. The new version must be checked again.

## `/bug approve [Plan Ref]`

Implement only the current version.

Require an empty `Needs Reconfirmation`, a passing `/plan-check` for the exact version, and a passing or not-applicable `/design-check` for that version when frontend behavior is affected.

Append approval to `Approval Ledger`, set status to `approved`, apply only the approved fix, then append changed files plus verification results to `Execution Ledger` and set status to `implemented`. Do not create another plan or broaden the fix during implementation. Finish by suggesting `/audit <Plan Ref>`.
