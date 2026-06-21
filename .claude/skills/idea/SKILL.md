---
name: idea
description: Project-local idea shaping workflow. Use when the user invokes /idea or has a fuzzy thought, vague problem, product concern, or goal and needs a human-readable requirement Human Plan before /dev.
---

# Idea

## `/idea xxx`

Help the human turn a vague thought or problem into a concrete, discussable, implementable requirement Human Plan.

Do not write code.

Work by clarifying:

- the real problem
- target user or workflow
- current pain
- desired outcome
- possible solution directions
- tradeoffs
- smallest useful version
- open decisions

Human Plan focuses on:

- problem framing
- possible approaches
- recommended direction
- concrete requirement draft
- open questions
- suggested next step

After outputting the Human Plan, wait for feedback.

## `/idea replan`

Rewrite the idea Human Plan using the user's feedback.

After outputting the revised Human Plan, wait for feedback. The agreed Human Plan should be ready to feed into `/dev`.
