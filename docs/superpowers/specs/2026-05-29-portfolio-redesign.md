# Portfolio Page Redesign

## Scope

Redesign the `/portfolio` page (WatchlistManagePage) from a cluttered all-in-one page into a focused stock-grouping manager with a clean app-like UI. The page manages user-defined stock groups (including a default "all watchlist" group) used to select batch-analysis target sets.

## Current State Problems

- 1074-line monolithic component with 6 sections crammed into one page
- A-share sync and stock browsing duplicated with existing MarketStocksPage (`/stocks`)
- Market-classification logic duplicated across WatchlistManagePage and WatchlistPanel
- WatchlistPanel component (297 lines) is completely unused — dead code

## What Gets Removed

### Dead code removal
- **WatchlistPanel.tsx** — defined but never imported; not re-exported from `components/dashboard/index.ts`

### Moved out of portfolio page
- A-share sync section → already exists in `/stocks` (MarketStocksPage)
- A-share stock browser grid → already exists in `/stocks`

### Code deduplication
- Extract shared market classification (`MARKET_RULES`, `classifyStock`, `MARKET_LABELS`, `MARKET_COLORS`) into a single `utils/market.ts` util, consumed by all pages that need it

## New Portfolio Page Design

### Layout

```
┌──────────────────────────────────────────────────────────┐
│  ← Back    Stock Group Manager                           │
│            Manage stock groups for batch analysis         │
├──────────────────────────────────────────────────────────┤
│  [All Watchlist(32)] [Tech(12)] [Consumer(8)] ... [+New] │  ← horizontal tab bar
├──────────────────────────────────────────────────────────┤
│  🔍 Search stocks to add to group...       [Manage]      │  ← search bar + action button
├──────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                 │
│  │ 600519   │ │ 000858   │ │ 300750   │                 │
│  │ 贵州茅台  │ │ 五粮液    │ │ 宁德时代  │                 │  ← 3-column card grid
│  │ 沪市主板  │ │ 深市主板  │ │ 创业板    │                 │
│  └──────────┘ └──────────┘ └──────────┘                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                 │
│  │ 002594   │ │ ...      │ │ ...      │                 │
│  │ 比亚迪    │ │          │ │          │                 │
│  │ 深市主板  │ │          │ │          │                 │
│  └──────────┘ └──────────┘ └──────────┘                 │
└──────────────────────────────────────────────────────────┘
```

### Interaction flows

1. **Default entry**: "All Watchlist" tab selected, all self-selected stocks shown in card grid
2. **Switch group**: click horizontal tab to switch; active tab is highlighted
3. **Quick add**: type in search bar, pick from API-backed autocomplete suggestions, stock is added to current group (and to the global watchlist if not already)
4. **Manage mode** (click "Manage" → opens Drawer):
   - Create / rename / delete groups
   - Batch delete stocks from current group
   - Batch paste to add codes
   - Move selected stocks between groups
5. **New group**: click "+" tab at end, enter name, group is created
6. **Remove stock from group**: hover on card → reveal X button → click to remove

### Card design (matching MarketStocksPage style)

Each card shows:
- Stock code (monospace, bold)
- Stock name (truncated)
- Market badge (colored chip)
- On hover: overlay X button in top-right corner to remove from group

### Component decomposition

- `WatchlistManagePage` — page shell: tab bar, search, manage entry, delegates to StockCardGrid and ManageDrawer
- `StockCardGrid` — responsive 3-column card grid for stock display
- `ManageDrawer` — edit modal: rename/delete group, batch add/remove, stock multi-select
- `GroupTabBar` — horizontal scrollable tab bar with "+" new-group tab

### Data flow

- Group data persisted via `localStorage` (existing `watchlistGroups.ts` — keep as-is)
- Global watchlist (STOCK_LIST) fetched from API (existing `watchlistApi`)
- Stock search uses existing `stocksApi.list()` for autocomplete
- Adding to group that is "All Watchlist" also calls `watchlistApi.add()`

### Files changed

| File | Action |
|------|--------|
| `components/dashboard/WatchlistPanel.tsx` | Delete (dead code) |
| `utils/market.ts` | New — shared market classification |
| `pages/WatchlistManagePage.tsx` | Rewrite — clean group manager |
| `pages/MarketStocksPage.tsx` | May import from `utils/market.ts` |
| Shell.tsx nav label | Optional — rename "管理自选股" → "自选分组" |

### What stays unchanged

- `utils/watchlistGroups.ts` — group persistence logic (used by BatchPanel, BatchRunDetailPage, WorkflowBuilderPage)
- `api/watchlist.ts` — watchlist CRUD API
- `api/stocks.ts` — stock list/sync API
- `stores/batchStore.ts` — batch run state
- `components/batch/BatchPanel.tsx` — batch panel consuming watchlist groups
