---
name: project-map
description: Use when the user invokes /project-map or asks to initialize/update/visualize page function docs, maintain docs/project-map, or check product requirement and page-design problems from those docs.
---

# Project Map

`/project-map` exists for one purpose: make an AI-written project controllable by humans through `docs/project-map`.

The md files are not notes, changelogs, plans, or summaries. They are page-level requirement documents. A human should be able to read them line by line to verify the product. Another AI should be able to rebuild the same page behavior from them without opening source code.

## Commands

Only these commands exist:

```text
/project-map init
/project-map update
/project-map visualize
/project-map check
```

Any other command name is outside this skill.

## `/project-map init`

Create `docs/project-map` from the current project code.

Workflow:

1. Find every frontend route and page entry.
2. For each page, read the page file and all directly relevant components, hooks, stores, utils, and API clients that decide visible behavior.
3. Create `docs/project-map/README.md` as a route index only.
4. Create one md file per page under `docs/project-map/pages/`.
5. After all page docs exist, scan across pages and fill every `相似功能` and `差异点`.

Hard requirements:

- Do not document from filenames alone.
- Do not stop at the page file if behavior is inside imported components/hooks.
- Do not write vague phrases such as `组件内部处理`, `调用接口`, `展示数据`, `处理错误`, `相关数据`, or `等等`.
- Do not write `最近变更`, TODO, implementation plan, development notes, or generic principles.
- Do not guess endpoint paths, params, storage keys, or response fields. Read the source that proves them.
- Preserve exact visible text: titles, tabs, buttons, labels, placeholders, badges, loading text, empty text, error text, dialog text, success text, and status wording.
- Large pages need more sections, not shorter summaries.

## `/project-map update`

Synchronize page docs with current code changes. The user does not specify a route or file; infer the affected pages yourself.

Workflow:

1. Inspect code changes with `git status --short`, `git diff --name-only`, and `git diff --cached --name-only`.
2. If the worktree is clean, inspect the most recent committed change with `git diff --name-only HEAD~1..HEAD` and state that assumption in the response.
3. Map changed files to affected pages:
   - changed page or route file -> that page doc
   - changed component -> every page that imports or renders it
   - changed hook/store/util/API client -> every page whose behavior depends on it
   - changed backend endpoint -> every page md that references the endpoint or client
4. Read the existing md for every affected page first.
5. Read the current page code and every dependency that affects the changed behavior.
6. Rewrite affected sections so the md describes the complete current behavior, not just the diff.
7. If a feature was added or removed, add or remove that feature section.
8. Re-check `相似功能` and `差异点` against other page docs.
9. After page md updates are complete, automatically run the same work as `/project-map visualize` and refresh the interactive visual app in `docs/project-map/visual/`.

Do not ask the user to provide the route or file for update. If the affected page cannot be inferred, report the exact changed files and what evidence is missing.

When writing new page code in this repo, keep the matching project-map md and visual maps synchronized unless the user explicitly says not to.

## `/project-map visualize`

Create a browser-openable interactive flowchart from `docs/project-map`.

This command does not create more md summaries. The page md files remain the detailed source of truth. The visualization must be a real UI that a human can open, search, filter, click, and use to understand the whole project structure, repeated functions, data flows, and page relationships without reading every md first.

The primary visualization must be a deterministic flowchart, not a force-directed graph. A force-directed node cloud becomes unusable on real projects and is not acceptable as the default or main view.

Input:

- Read `docs/project-map/README.md`.
- Read all `docs/project-map/pages/*.md`.
- Do not use source code as the primary input. If the md omits something, show the gap in the visualization instead of silently filling it from code.

Output location:

```text
docs/project-map/visual/
  index.html
  project-map-data.json
  assets/
    visual.css
    visual.js
```

Hard output rules:

- Do not output markdown files as the visualization.
- Do not use Mermaid-in-md as the visualization.
- Do not make a static text report with tables only.
- `index.html` must be the main artifact. It must work by opening the local file in a browser unless the project clearly requires a dev server.
- `project-map-data.json` must contain structured nodes and edges parsed from the page md files.
- `visual.css` and `visual.js` must implement the actual visual UI. Inline them into `index.html` only if that is simpler and still maintainable.

Required visual UI:

- Project flowchart: a left-to-right or top-to-bottom flowchart of major user goals -> pages -> key functions -> outputs/decisions.
- Page flowcharts: one flowchart per page showing entry conditions -> user actions -> functions -> API/storage/state -> visible result.
- Data flowchart: deterministic flowchart links from page/function to API endpoint, request params, response fields, storage/cache keys, and UI usage.
- Similarity flowchart: duplicated or similar functions grouped into readable lanes, with difference details shown on click.
- State flowchart: state transitions for loading, empty, error, cached, polling, SSE, task, and streaming behavior.
- Page explorer: route -> page -> function tree with click-to-focus behavior.
- Evidence gap view: visual list or highlighted nodes for missing fields that block understanding.
- Details panel: clicking any node or edge shows exact source md file, section, extracted fields, and a link/path back to the md.
- Search and filters: search by page, function, endpoint, storage key, state word, or visible text; filter by node type and relationship type.

Flowchart layout rules:

- Use deterministic layered layout, not physics simulation.
- Prefer swimlanes such as `用户目标`, `页面`, `功能`, `数据/API`, `存储/缓存`, `状态`, `输出/结论`, `相似功能`.
- The project overview must stay readable. It should show pages and major flows only, not every endpoint and field.
- Put detailed functions into page-level flowcharts instead of the global overview.
- Split any chart that would exceed about 40 visible nodes or cause overlapping labels.
- Use clear arrows with verb labels such as `进入`, `触发`, `调用`, `写入`, `读取`, `渲染`, `跳转`, `相似`.
- Keep node labels readable without zooming. Long labels should wrap or truncate with full text in the details panel.
- Use color by semantic type, but hierarchy and arrow direction must still be understandable without color.
- A force-directed graph may exist only as an optional secondary exploration tab. It cannot be the default view and cannot be the only visual map.

Minimum visual coverage:

- Every page md appears as a page node.
- Every function section appears as a function node.
- Every backend endpoint appears as an endpoint node.
- Every storage/cache key appears as a storage/cache node.
- Every `相似功能` relationship appears as an edge or cluster.
- Every documented async/task/SSE/polling/loading/error/empty state appears in the state view.
- Every visual node points back to the source page md and section when possible.

Completion check for `visualize`:

1. Count page md files and verify the same count appears in `project-map-data.json`.
2. Check that every function heading from page md files appears in `project-map-data.json`.
3. Check that every endpoint and storage key from page md files appears in `project-map-data.json`.
4. Open `index.html` or inspect it to verify it loads `project-map-data.json` and renders flowcharts, not plain markdown.
5. Verify the default view is a readable flowchart, not a force-directed node cloud.
6. Verify the global overview is split from page-level details.
7. Check that search, filters, node click, and details panel are implemented.
8. Check that every non-empty `相似功能` appears as a relationship or cluster.
9. Check that visual nodes link or point back to source page md files.

## `/project-map check`

Use `docs/project-map` as the requirement source and help the human find unreasonable product/page design.

This command is not for checking whether the md format is pretty. The md is the evidence. The output is a requirement/design review for humans.

Workflow:

1. Read `docs/project-map/README.md` and all page md files.
2. Group page functions by user goal, business concept, data source, backend endpoint, response fields, storage/cache, task/stream flow, state transition, error handling, navigation, and conclusion wording.
3. Find problems in the requirements themselves.
4. Report concrete issues that need human decision.
5. Do not edit code or docs unless the user asks for fixes.

Find these kinds of problems:

- 同一个用户目标被拆到多个页面，但边界不清。
- 多个页面做同一种分析、读取同一种数据、调用同一个接口，但字段、口径、结论、状态文案、错误处理不同。
- 两个功能解决同一个业务问题，但使用不同数据源或不同判断逻辑。
- 页面职责混杂：一个页面同时承担多个不相干的决策流程。
- 功能重复，但 md 中看不出保留两个入口的必要差异。
- 同一种 loading、empty、error、disabled、retry、cached、streaming、polling 状态在不同页面表现不一致。
- localStorage/sessionStorage/cache key 的读写归属不清，或跨页面共享但没有明确契约。
- SSE、轮询、异步任务、模型流式输出的开始、恢复、失败、重试、完成语义不一致。
- 同一业务概念的字段名、单位、排序、过滤、阈值、颜色、状态映射、结论文案不一致。
- 用户必须在多个页面跳转才能完成一个连续任务。
- 页面把实现细节暴露成用户需求，反而没有表达用户真正要做的判断。
- 关键决策缺少必要的空态、异常态、不确定态或数据不足态。

Only mention md insufficiency when it blocks judging a real requirement problem. Phrase it like this:

```md
## 问题：证据不足，无法判断 <具体设计风险>
- 类型：需求证据不足
- 涉及页面：
- 当前需求表现：
- 阻塞判断的缺口：
- 风险：
- 建议人类决策：
- 证据：
```

Normal finding format:

```md
## 问题：<一句话说清楚不合理点>
- 类型：需求冲突 / 页面职责混乱 / 重复功能 / 口径冲突 / 数据源冲突 / 状态不一致 / 缓存不一致 / 错误处理不一致 / 用户路径割裂 / 信息架构不合理
- 涉及页面：
- 当前需求表现：
- 为什么不合理：
- 风险：
- 建议人类决策：
- 证据：
```

## Page Md Contract

Each page md must use this structure. Add more function sections as needed.

```md
# <页面名>

## 页面总览
- 路由：
- 页面文件：
- 主要组件：
- 页面入口条件：
- URL 参数：
- 页面级状态：
- 页面级副作用：
- 页面布局：

## <功能名>
- 代码位置：
- 用户目标：
- 用户操作：
- 触发条件：
- 页面展示：
- 精确文案：
- 数据来源：
- 数据字段：
- 后端接口：
- 请求参数：
- 响应使用：
- 存储/缓存：
- 状态流转：
- 渲染分支：
- 空状态：
- 异常状态：
- 导航行为：
- 权限/前置条件：
- 相似功能：
- 差异点：
```

Field meaning:

- `页面总览`: route shell, page entry, layout, page-level state/effects, and entry conditions.
- `代码位置`: exact page/component/hook/API files that prove this function.
- `用户目标`: what the user is trying to accomplish.
- `用户操作`: click, input, select, tab switch, submit, refresh, expand, navigate, or passive page load.
- `触发条件`: mount, URL param, search param, selected item, timer, focus, visibility, SSE event, polling tick, request completion, etc.
- `页面展示`: visible UI after this function runs.
- `精确文案`: exact user-visible words tied to the function.
- `数据来源`: API, backend route, localStorage, sessionStorage, URL, store, props, static config, derived state, or none.
- `数据字段`: exact fields consumed by UI or logic, including derived fields.
- `后端接口`: exact method and path. Use `无` only when no backend call exists.
- `请求参数`: path/query/body params, defaults, and where each value comes from.
- `响应使用`: mapping from response to UI/state, including sort, filter, fallback, formatting, truncation, aggregation, and status mapping.
- `存储/缓存`: browser storage keys, memory cache, backend persistence, request cache flag, stream/task state, or none.
- `状态流转`: loading, success, failure, retry, disabled, selected, expanded, dirty, cached, streaming, pending, processing, completed, failed, etc.
- `渲染分支`: every conditional branch and when it appears.
- `空状态`: what the user sees when there is no data.
- `异常状态`: what the user sees or what silently happens on failure.
- `导航行为`: route changes, URL sync, redirects, back behavior, external links, or none.
- `权限/前置条件`: login, selected symbol, selected group, non-empty form, initialized config, valid date range, etc.
- `相似功能`: other page/function with the same user goal, data type, endpoint, storage, task flow, or conclusion.
- `差异点`: exact behavioral difference from those similar functions.

If source code cannot prove a field, write `代码中未明确：<file/function>` with the exact missing point.

## Evidence Rules

Build page docs from evidence in this order:

1. Router and route files.
2. Page file.
3. Imported components that own visible UI or user actions.
4. Hooks/stores/utils that own state machines, storage, timers, polling, SSE, tasks, cache, or derived data.
5. API clients and backend routes for exact method/path/params/fields.

Do not summarize delegated behavior as “inside the component”. Open the component and document the behavior that affects the page.

## Completion Check for `init` and `update`

Before claiming completion:

1. Pick the largest touched page and verify at least three function sections against source.
2. Search generated md for vague phrases and fix them.
3. Verify endpoint paths and params from API clients or backend routes.
4. Verify repeated endpoints/data sources/business goals have `相似功能` and `差异点`.
5. For `update`, verify the interactive visual app in `docs/project-map/visual/` was refreshed after page md changes.
6. Ask the reconstruction question: can another AI rebuild the page behavior from the md alone?

If the answer is no, the docs are not done.
