---
name: bug-fix
description: Project-local bug fixing workflow. Use when the user invokes /bug after a bug has been identified and wants a human-readable fix plan first, then implementation after /bug approve.
---

# Bug Fix

## `/bug xxx`

Write a Human Plan for the already identified bug. Do not edit code yet.

Human Plan must include:

- Requirement Baseline: expected behavior, broken behavior being fixed, and non-negotiable user-visible outcome
- Confirmed Decisions: bug scope and fix constraints the human already approved
- Current Plan: symptom, confirmed or likely root cause, affected page/API/data flow/module, user-visible impact, fix direction, verification approach, and regression risk
- Changes Since Last Plan: what changed in this revision
- Unchanged Scope: what must stay the same
- Needs Reconfirmation: anything that changes expected behavior, bug scope, or user-visible outcome

After outputting the Human Plan, wait for feedback or `/bug approve`.

## `/bug approve`

Implement the approved bug fix plan.

Apply the fix, verify the original bug is resolved, and check the closest regression risk.

Finish with a short summary of root cause, changed files, verification result, and remaining risk.

## `/bug replan`

Rewrite the bug fix Human Plan using the user's feedback or `/plan-check` suggestions.

Preserve the bug Requirement Baseline unless the user explicitly changes it. Show what changed since the previous plan and what still stays the same. Put any behavior-changing suggestion under `Needs Reconfirmation`.

After outputting the revised Human Plan, wait for feedback or `/bug approve`.
