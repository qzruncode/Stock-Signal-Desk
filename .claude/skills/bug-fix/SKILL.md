---
name: bug-fix
description: Project-local bug fixing workflow. Use when the user invokes /bug after a bug has been identified and wants a human-readable fix plan first, then implementation after /bug approve.
---

# Bug Fix

## `/bug xxx`

Write a Human Plan for the already identified bug. Do not edit code yet.

Human Plan focuses on:

- bug symptom
- confirmed or likely root cause
- affected page, API, data flow, or module
- user-visible impact
- fix direction
- verification approach
- regression risk to watch

After outputting the Human Plan, wait for feedback or `/bug approve`.

## `/bug approve`

Implement the approved bug fix plan.

Apply the fix, verify the original bug is resolved, and check the closest regression risk.

Finish with a short summary of root cause, changed files, verification result, and remaining risk.
