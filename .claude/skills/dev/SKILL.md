---
name: dev
description: Project-local /dev workflow. Use when the user invokes /dev with a requirement, first producing a human-readable Human Plan, then implementing after /dev approve.
---

# Dev

## `/dev xxx`

Read the requirement and output a Human Plan for review.

Human Plan focuses on:

- requirement goal
- business direction
- affected pages or functions
- user-visible behavior
- key business details
- important decisions for the human
- implementation guidance at product level

After outputting the Human Plan, wait for feedback or `/dev approve`.

## `/dev approve`

Implement the approved Human Plan.

For simple work, implement directly.

For complex work, create an internal AI-readable plan and execute it step by step.

Finish with a short summary of changes and verification.
