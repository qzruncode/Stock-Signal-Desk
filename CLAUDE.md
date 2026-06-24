# Daily Stock Analysis AI Instructions

This file is intentionally compatible with Claude Code and Codex. Follow it when editing this repository.

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
