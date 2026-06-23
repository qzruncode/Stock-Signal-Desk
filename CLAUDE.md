# Daily Stock Analysis AI Instructions

This file is intentionally compatible with Claude Code and Codex. Follow it when editing this repository.

## Project Skills

- Use `.claude/skills/idea` when the user invokes `/idea` or has a vague thought, problem, or goal that needs to become a concrete requirement before `/dev`.
- Use `.claude/skills/dev` when the user invokes `/dev`, asks for a short Human Plan before implementation, or wants Human Plan approval followed by AI-only Execution Plan execution.
- Use `.claude/skills/bug-fix` when the user invokes `/bug` for an identified bug and wants a Human Plan before approved implementation.
- Use `.claude/skills/plan-check` when the user invokes `/plan-check` or asks to review a Human Plan before implementation.
- Use `.claude/skills/design-check` when the user invokes `/design-check` or asks to review frontend design, visual quality, UI consistency, or interaction quality before implementation.
- Use `.claude/skills/audit` when the user invokes `/audit` or asks to review AI-written code and produce an approved follow-up fix plan.
- Use `.claude/skills/code-scan` when the user invokes `/code-scan` or asks to scan the current project for concrete code problems and produce a Human Plan.
- Use `.claude/skills/arch-check` when the user invokes `/arch-check` or asks whether the project is reinventing wheels or diverging from mature business/code/architecture solutions.

## Requirement Baseline

Every Human Plan must preserve the original user intent as a Requirement Baseline.

Human Plans must carry:

- Requirement Baseline: the original problem, goal, user value, and non-negotiable behavior.
- Confirmed Decisions: choices the human already approved.
- Current Plan: what this plan proposes now.
- Changes Since Last Plan: what changed in this revision.
- Unchanged Scope: what must stay the same.
- Needs Reconfirmation: anything that changes the baseline, business goal, user path, or visible behavior.

Replan may adjust implementation direction, structure, scope split, or design details. Replan must not silently change the Requirement Baseline. If the baseline should change, call it out as `Needs Reconfirmation`.

If a Human Plan exceeds 50 lines, write the full plan to:

```text
docs/human-plans/YYYY-MM-DD-<skill>-<short-topic>.md
```

In chat, return only the file path, a short summary, and the suggested next command. The file must contain the full Human Plan and preserve the Requirement Baseline fields above.

## Code Rules

- Keep production `apps/dsa-web/src/**/*.ts(x)` files under 600 lines. Exclude test files.
- Page files should orchestrate data and layout only. Move reusable UI into `components/**`.
- Move state machines, timers, subscriptions, polling, and localStorage logic into hooks or utils.
- Do not create components inside render functions.
- Component files should export components only; shared helpers belong in `utils/**` to keep Fast Refresh clean.
- Route-level lazy loading already exists in `App.tsx`; keep heavy page dependencies behind route chunks.

- Run `npm run check:constraints` in `apps/dsa-web` after structural frontend changes.
- Production page chunks should stay below 150 KiB uncompressed.
- Vendor chunks should stay below 260 KiB uncompressed.
- If a budget is exceeded, split the feature or move heavy dependencies behind lazy boundaries.

## Validation

Use the bundled Node runtime in Codex when the system Node is too old:

```bash
PATH="/Users/xiejiawei/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH" npm run lint
PATH="/Users/xiejiawei/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH" npm run build
PATH="/Users/xiejiawei/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH" npm run check:constraints
```

For Python changes:

```bash
python3 -m compileall -q src api data_provider main.py server.py
```
