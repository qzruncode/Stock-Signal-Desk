---
name: dev
description: Use when the user invokes /dev with a requirement or an accepted Human Plan for a feature, behavior change, refactor, or other development work.
---

# Dev

## Canonical Plan

Use one canonical Human Plan file as the source of truth for the whole workflow.

- Create `docs/human-plans/HP-YYYYMMDD-HHMM-<topic>.md` only when no Plan Ref exists.
- When a Plan Ref exists, continue that exact file and `Plan ID`; never create a separate development plan.
- Resolve the plan from an explicit path first. Otherwise use the single unambiguous Plan Ref in the current conversation. Never guess between multiple plans.
- If the referenced version differs from `Current Version`, stop and report a stale Plan Ref. Never silently switch versions.
- End every response with `Plan Ref: <path>#v<current-version>`.
- Never rewrite an earlier version or ledger entry. Add a new version for every replan or stage transition.
- Keep `Original Request` and `Requirement Baseline` unchanged. A baseline change requires explicit human confirmation and an append-only `Baseline Amendments` entry.
- Reviews and approvals apply only to the exact version they name. A new version invalidates earlier results.

The file must carry Plan ID, version/stage/status metadata, Original Request, Requirement Baseline, Baseline Amendments, Plan Versions, Review Ledger, Approval Ledger, Execution Ledger, and Audit Ledger. Each version must contain `Confirmed Decisions`, `Current Plan`, `Changes Since Previous Version`, `Unchanged Scope`, and `Needs Reconfirmation`.

If a legacy plan lacks this structure, add the metadata and ledgers without changing its content; preserve the existing plan as version 1.

## `/dev xxx`

If `xxx` contains a Plan Ref, read the full file and append a new version with stage `development` and status `review pending`. Preserve the upstream baseline, decisions, scope, and business direction.

If no Plan Ref exists, create the canonical file and version 1 with stage `development` and status `review pending`. Store the original request verbatim.

The current version must describe the goal, business behavior, affected existing structure, reuse and integration direction, user-visible behavior, key edge cases, data/API impact, and verification approach. Do not include low-level construction noise that prevents human review.

Set `Frontend Impact` to `yes`, `no`, or `unknown`. Return the Plan Ref and require `/plan-check <Plan Ref>`, followed by `/design-check <Plan Ref>` when frontend impact is `yes` or `unknown`.

## `/dev replan [Plan Ref]`

Read the canonical plan and all current-version review entries. Append a new version in the same file that applies accepted feedback.

Do not mutate earlier versions. Preserve the baseline and confirmed decisions. Put any unconfirmed baseline change under `Needs Reconfirmation`. Set status to `review pending`. The new version must be checked again.

## `/dev approve [Plan Ref]`

Implement only the current version of the canonical plan.

Before editing code, verify:

- the referenced version is still current
- `Needs Reconfirmation` is empty
- `/plan-check` passed for that exact version
- `/design-check` passed or was marked not applicable for that exact version when frontend impact is `yes` or `unknown`

If any condition fails, do not implement. Report the missing step with the same Plan Ref.

Append approval to `Approval Ledger`, set status to `approved`, implement without silently changing scope, then append changed files and verification results to `Execution Ledger` and set status to `implemented`. Do not create another Human Plan or change the approved version during implementation. Finish by suggesting `/audit <Plan Ref>`.
