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

Human Plan focuses on:

- overall code health judgment
- highest priority problems
- affected files or modules
- why each problem matters
- suggested fix direction
- risk and verification approach
- recommended next `/dev` target

After outputting the Human Plan, wait for feedback.

## `/code-scan replan`

Rewrite the code scan Human Plan using the user's feedback.

After outputting the revised Human Plan, wait for feedback. The agreed Human Plan should be ready to feed into `/dev`.
