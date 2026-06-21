---
name: design-check
description: Project-local frontend design and UX review. Use when the user invokes /design-check or asks to review whether a Human Plan or page change will produce ugly UI, poor interaction, inconsistent style, or hard-to-use frontend behavior.
---

# Design Check

## `/design-check`

Review the current Human Plan or proposed frontend change from a product designer and frontend engineer perspective before implementation.

Before judging the plan, inspect the existing page, components, styles, layout patterns, and interaction patterns that the change would touch.

Check:

- visual fit: whether the change matches the existing product style, spacing, density, typography, color, and component patterns
- interaction fit: whether the user path is clear, efficient, and not mentally heavy
- information design: whether the page shows the right information hierarchy, avoids clutter, and keeps key decisions easy to scan
- responsive behavior: whether desktop and mobile layouts remain usable
- state design: loading, empty, error, disabled, success, long-running, and partial-data states
- component reuse: whether existing UI components or patterns should be reused instead of inventing a new look
- page consistency: whether similar pages or features will feel like one product
- accessibility: readable text, usable controls, keyboard/focus behavior where relevant
- implementation risk: whether the plan is likely to create one-off CSS, fragile layout, nested cards, awkward spacing, or inconsistent controls

Output:

- conclusion: 可执行 / 需要重写设计方案
- existing UI fit
- interaction concerns
- visual concerns
- concrete design replan suggestions

If the plan is likely to produce ugly, inconsistent, or hard-to-use UI, mark it as `需要重写设计方案` and say what the next Human Plan should change.
