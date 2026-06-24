---
name: idea
description: Use when the user invokes /idea or has a fuzzy thought, vague problem, product concern, or goal that is not yet a concrete requirement.
---

# Idea

## Canonical Plan

Use one canonical Human Plan file as the source of truth for the whole workflow.

- Create `docs/human-plans/HP-YYYYMMDD-HHMM-<topic>.md` on the first invocation, regardless of plan length.
- Keep the same file and `Plan ID` through idea, development, checks, approval, implementation, and audit.
- Resolve the plan from an explicit path first. Otherwise use the single unambiguous `Plan Ref` in the current conversation. Never guess between multiple plans.
- If the referenced version differs from `Current Version`, stop and report a stale Plan Ref. Never silently switch versions.
- End every response with `Plan Ref: <path>#v<current-version>`.
- Never rewrite an earlier version or ledger entry. Add a new version for every replan or stage transition.
- Keep `Original Request` and `Requirement Baseline` unchanged. A baseline change requires explicit human confirmation and an append-only entry under `Baseline Amendments`.
- Reviews and approvals apply only to the exact version they name. A new version invalidates earlier review and approval results.

The file must contain:

- Plan ID, Current Version, Current Stage, Status, Frontend Impact
- Original Request
- Requirement Baseline
- Baseline Amendments
- Plan Versions
- Review Ledger
- Approval Ledger
- Execution Ledger
- Audit Ledger

Each plan version must contain `Confirmed Decisions`, `Current Plan`, `Changes Since Previous Version`, `Unchanged Scope`, and `Needs Reconfirmation`.

If a legacy plan lacks this structure, add the metadata and ledgers without changing its content; preserve the existing plan as version 1.

## `/idea xxx`

Create version 1 with stage `idea` and status `draft`. Store the user's original wording verbatim, then clarify:

- the real problem and target user
- current pain and desired outcome
- possible directions and tradeoffs
- smallest useful scope
- open decisions

Do not write code. Write the complete plan to the canonical file and return only a short summary, the Plan Ref, and the suggested next command.

## `/idea replan [Plan Ref]`

Read the complete canonical plan and human feedback. Append a new version in the same file.

Preserve the baseline and confirmed decisions. Put any unconfirmed baseline change under `Needs Reconfirmation`. Set status to `draft`. When the requirement is accepted, suggest `/dev <Plan Ref>` so development continues on the same plan.
