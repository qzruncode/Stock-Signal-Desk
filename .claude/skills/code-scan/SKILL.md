---
name: code-scan
description: Project-local codebase scan. Use when the user invokes /code-scan or asks to scan the current project for bad code, bugs, inefficiency, redundancy, maintainability issues, or other concrete code problems before planning fixes.
---

# Code Scan

## `/code-scan`

Scan the current project code and produce a Human Plan for the most important fixes.

Do not edit code.

Look for:

- bugs and wrong behavior risks
- duplicated or redundant logic
- inefficient code paths
- oversized files or functions
- unclear module boundaries
- poor abstraction or missing reuse
- fragile async, cache, state, or data flow
- frontend performance and interaction problems
- backend API, validation, error handling, and data consistency problems
- security, reliability, and deployment risks

If there are many issues, report the most serious ones first.

Human Plan must include:

- Requirement Baseline: overall code health judgment, selected problem scope, and why these fixes matter
- Confirmed Decisions: priority order or scope already approved by the human
- Current Plan: highest priority problems, affected files/modules, suggested fix direction, risk, verification approach, and recommended next `/dev` target
- Changes Since Last Plan: what changed in this revision
- Unchanged Scope: what must stay the same
- Needs Reconfirmation: anything that changes the selected problem scope or priority

After outputting the Human Plan, wait for feedback.

## `/code-scan replan`

Rewrite the code scan Human Plan using the user's feedback.

Preserve the Requirement Baseline unless the user explicitly changes it. Show what changed since the previous plan and what still stays the same.

After outputting the revised Human Plan, wait for feedback. The agreed Human Plan should be ready to feed into `/dev`.
