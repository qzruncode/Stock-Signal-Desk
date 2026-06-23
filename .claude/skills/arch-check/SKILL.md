---
name: arch-check
description: Project-local mature-solution check with external GitHub research. Use when the user invokes /arch-check or asks whether the project is reinventing wheels, missing mature business/code/architecture solutions, or diverging from proven open-source patterns.
---

# Arch Check

## `/arch-check`

Inspect the current project and produce a Human Plan based on mature solutions.

Do not edit code.

Use local code first, then use available network/GitHub MCP tools to inspect mature open-source solutions or references relevant to the business, product, code, or architecture problem.

Check:

- business workflow: whether the product logic can follow a mature workflow instead of custom ad hoc rules
- code implementation: whether existing libraries, patterns, or framework features solve what the code is hand-rolling
- product behavior: whether mature products handle the same user problem more clearly
- frontend/backend boundaries
- module layering and dependency direction
- API and service contracts
- state, task, cache, and data flow
- database/schema/query architecture
- async, retry, fallback, and reliability design
- security boundaries
- scalability and maintainability
- where this project reinvents wheels or diverges from mature solutions

Human Plan must include:

- Requirement Baseline: current business/code/architecture problem, local evidence, and why mature-solution alignment matters
- Confirmed Decisions: reference direction or migration scope already approved by the human
- Current Plan: mature reference projects/libraries/products/patterns, recommended direction, migration path, risk, verification approach, and recommended next `/dev` target
- Changes Since Last Plan: what changed in this revision
- Unchanged Scope: what must stay the same
- Needs Reconfirmation: anything that changes the business goal, mature-solution direction, or migration scope

Include links or repository names for external references used.

After outputting the Human Plan, wait for feedback.

## `/arch-check replan`

Rewrite the mature-solution Human Plan using the user's feedback.

Preserve the Requirement Baseline unless the user explicitly changes it. Show what changed since the previous plan and what still stays the same.

After outputting the revised Human Plan, wait for feedback. The agreed Human Plan should be ready to feed into `/dev`.
