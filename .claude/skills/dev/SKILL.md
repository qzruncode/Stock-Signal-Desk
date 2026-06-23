---
name: dev
description: Project-local /dev workflow. Use when the user invokes /dev with a requirement, first producing a human-readable Human Plan, then implementing after /dev approve.
---

# Dev

## `/dev xxx`

Read the requirement and output a Human Plan for review.

Human Plan must include:

- Requirement Baseline: original problem, goal, user value, and non-negotiable behavior from the accepted idea, scan, mature-solution check, or user request
- Confirmed Decisions: choices the human already approved
- Current Plan: requirement goal, business direction, affected pages/functions, user-visible behavior, key business details, and product-level implementation guidance
- Changes Since Last Plan: what changed in this revision
- Unchanged Scope: what must stay the same
- Needs Reconfirmation: anything that changes the baseline, business goal, user path, or visible behavior

After outputting the Human Plan, wait for feedback or `/dev approve`.

## `/dev approve`

Implement the approved Human Plan.

For simple work, implement directly.

For complex work, create an internal AI-readable plan and execute it step by step.

Finish with a short summary of changes and verification.

## `/dev replan`

Rewrite the Human Plan using the user's feedback or `/plan-check` suggestions.

Preserve the Requirement Baseline unless the user explicitly changes it. Show what changed since the previous plan and what still stays the same. Put any baseline-changing suggestion under `Needs Reconfirmation`.

After outputting the revised Human Plan, wait for feedback or `/dev approve`.
