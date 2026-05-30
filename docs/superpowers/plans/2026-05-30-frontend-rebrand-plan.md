# 前端全面重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 全面重构 apps/dsa-web 前端 — shadcn/ui 组件库 + motion 动画 + 金融终端风暗色主题 + CSS 瘦身 + 页面拆分

**Architecture:** 先建基础设施（shadcn/ui + CSS + 主题 + 动画层），再逐页替换组件，最后删除旧文件。每个页面独立可测试。

**Tech Stack:** React 19, TypeScript, Tailwind v4, shadcn/ui, motion, zustand, next-themes, recharts

---

## 文件映射

### 新建文件

| 文件 | 用途 |
|------|------|
| `vite.config.ts` (修改) | 添加 `@` alias |
| `src/components/ui/*` | shadcn/ui 自动生成的 15 个组件 |
| `src/styles/globals.css` | 替代 index.css，@theme 驱动 (~600行) |
| `src/components/layout/Shell.tsx` (重写) | 侧栏导航 + ThemeToggle |
| `src/components/layout/ThemeToggle.tsx` | 暗色/亮色切换按钮 |
| `src/components/features/` | 业务组件目录（保留现有 report/batch/template） |
| `src/pages/HomePage/index.tsx` | HomePage 布局编排 (~80行) |
| `src/pages/HomePage/SearchPanel.tsx` | 搜索/模板/分析按钮 |
| `src/pages/HomePage/ReportArea.tsx` | 报告/对话展示 |
| `src/pages/HomePage/TaskPanel.tsx` | 任务面板 |
| `src/pages/HomePage/HistoryList.tsx` | 历史记录列表 |
| `src/pages/HomePage/WorkflowGuide.tsx` | 右侧工作流指引 |
| `src/pages/HomePage/useHomeActions.ts` | 业务逻辑 hook |
| `src/pages/HomePage/useTaskManager.ts` | 任务管理 hook |

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/App.tsx` | 添加 AnimatePresence + motion 路由过渡 |
| `src/main.tsx` | 更新 CSS import 路径 |
| `src/components/common/index.ts` | 更新导出（删除 shadcn 替代的组件） |
| `src/pages/MarketStocksPage.tsx` | 替换为 shadcn 组件 + 动画 |
| `src/pages/WatchlistManagePage.tsx` | 替换为 shadcn Tabs/Sheet/Popover |
| `src/pages/StockAnalysisPage.tsx` | 行情卡片升级 + 图表 + 动画 |
| `src/pages/SettingsPage.tsx` | shadcn 表单组件 |
| `src/pages/WorkflowBuilderPage.tsx` | motion drag 排序 |
| `src/pages/BatchRunDetailPage.tsx` | shadcn 组件 + 进度动画 |
| `src/pages/LoginPage.tsx` | 更新组件 import 路径 |
| `src/pages/NotFoundPage.tsx` | 更新组件 import 路径 |
| `tailwind.config.js` | 精简为 shadcn 最小配置 |

### 删除文件

```
src/components/common/Button.tsx
src/components/common/Card.tsx
src/components/common/Drawer.tsx
src/components/common/ConfirmDialog.tsx
src/components/common/Select.tsx
src/components/common/Input.tsx
src/components/common/Badge.tsx
src/components/common/Tooltip.tsx
src/components/common/InlineAlert.tsx
src/components/common/Loading.tsx
src/components/common/ScrollArea.tsx
src/components/common/Toolbar.tsx
src/App.css
src/index.css (被 styles/globals.css 替代)
```

---

## Phase 1: 基础设施

### Task 1: 安装 shadcn/ui + 配置 @ alias

**Files:**
- Modify: `apps/dsa-web/vite.config.ts`
- Create: `apps/dsa-web/components.json` (by shadcn init)
- Create: `apps/dsa-web/src/components/ui/button.tsx` (by shadcn add)

- [ ] **Step 1: 添加 @ alias 到 vite.config.ts**

修改 `apps/dsa-web/vite.config.ts`，在 plugins 后添加 resolve.alias：

```typescript
import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

const packageJson = JSON.parse(
  readFileSync(new URL('./package.json', import.meta.url), 'utf-8'),
) as { version?: string }
const buildTime = new Date().toISOString()

export default defineConfig({
  define: {
    __APP_PACKAGE_VERSION__: JSON.stringify(packageJson.version ?? '0.0.0'),
    __APP_BUILD_TIME__: JSON.stringify(buildTime),
  },
  plugins: [
    react({
      babel: {
        plugins: [['babel-plugin-react-compiler']],
      },
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: path.resolve(__dirname, '../../static'),
    emptyOutDir: true,
  },
})
```

- [ ] **Step 2: 确认 tsconfig.app.json 中有 @ alias**

检查 `apps/dsa-web/tsconfig.app.json`，如果没有 `"@/*"` paths，添加：

```json
{
  "compilerOptions": {
    // ... existing ...
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    }
  }
  // ...
}
```

- [ ] **Step 3: 初始化 shadcn/ui**

```bash
cd apps/dsa-web
npx shadcn@latest init
```

选择：
- Style: **New York**
- Base color: **Zinc**
- CSS variables: **Yes**
- Tailwind config: 选择现有的 `tailwind.config.js`

这会在 `apps/dsa-web/` 下创建 `components.json`。

- [ ] **Step 4: 安装 shadcn/ui 组件**

```bash
cd apps/dsa-web
npx shadcn@latest add button card dialog alert-dialog select input badge tooltip alert skeleton scroll-area tabs dropdown-menu popover separator sheet
```

这会在 `src/components/ui/` 下创建对应文件。

- [ ] **Step 5: 验证 build**

```bash
cd apps/dsa-web
npm run build
```

Expected: 成功编译，输出到 `../../static/`

- [ ] **Step 6: Commit**

```bash
git add apps/dsa-web/vite.config.ts apps/dsa-web/tsconfig.app.json apps/dsa-web/components.json apps/dsa-web/src/components/ui/
git commit -m "chore: install shadcn/ui and configure @ alias"
```

---

### Task 2: 重写 CSS — globals.css 精简

**Files:**
- Create: `apps/dsa-web/src/styles/globals.css`
- Modify: `apps/dsa-web/src/main.tsx` (update import)
- Modify: `apps/dsa-web/tailwind.config.js` (精简)

- [ ] **Step 1: 创建 styles/globals.css**

创建 `apps/dsa-web/src/styles/globals.css`：

```css
@import "tailwindcss";

@theme {
  /* === Core Design Tokens === */
  --color-background: 228 35% 7%;
  --color-foreground: 210 33% 98%;
  --color-primary: 190 100% 50%;
  --color-primary-foreground: 228 35% 8%;
  --color-secondary: 231 19% 15%;
  --color-secondary-foreground: 210 33% 94%;
  --color-muted: 230 18% 14%;
  --color-muted-foreground: 228 13% 66%;
  --color-card: 230 24% 10%;
  --color-card-foreground: 210 33% 98%;
  --color-popover: 230 24% 10%;
  --color-popover-foreground: 210 33% 98%;
  --color-destructive: 349 100% 63%;
  --color-destructive-foreground: 210 33% 98%;
  --color-border: 226 19% 20%;
  --color-input: 226 19% 20%;
  --color-ring: 190 100% 50%;

  /* === Financial Terminal Colors === */
  --color-cyan: 190 100% 50%;
  --color-cyan-glow: 190 100% 50% / 0.4;
  --color-purple: 247 84% 72%;
  --color-purple-glow: 247 84% 72% / 0.3;
  --color-success: 152 69% 40%;
  --color-warning: 37 92% 50%;
  --color-danger: 0 90% 55%;

  /* === Semantic Aliases === */
  --color-success: 149 100% 42%;
  --color-danger: 0 88% 62%;

  /* === Radius === */
  --radius-sm: calc(var(--radius) - 4px);
  --radius-md: calc(var(--radius) - 2px);
  --radius-lg: var(--radius);
  --radius-xl: 1.35rem;

  /* === Shadows === */
  --shadow-soft-card: 0 18px 48px hsl(215 25% 10% / 0.4);
  --shadow-soft-card-strong: 0 24px 56px hsl(215 25% 10% / 0.5);

  /* === Animations === */
  --animate-fade-in: fadeIn 0.3s ease-out;
  --animate-slide-up: slideUp 0.4s ease-out;
  --animate-pulse-glow: pulseGlow 2s ease-in-out infinite;
}

/* === Dark Theme (default) === */
/* Tokens above are the dark defaults. Light theme overrides below. */

/* === Light Theme === */
@media (prefers-color-scheme: light) {
  @theme {
    --color-background: 216 33% 97%;
    --color-foreground: 228 35% 12%;
    --color-primary: 193 100% 43%;
    --color-primary-foreground: 216 33% 97%;
    --color-secondary: 214 32% 91%;
    --color-secondary-foreground: 228 35% 16%;
    --color-muted: 214 30% 94%;
    --color-muted-foreground: 224 12% 42%;
    --color-card: 0 0% 100%;
    --color-card-foreground: 228 35% 12%;
    --color-popover: 0 0% 100%;
    --color-popover-foreground: 228 35% 12%;
    --color-destructive: 349 82% 56%;
    --color-destructive-foreground: 210 33% 98%;
    --color-border: 217 28% 84%;
    --color-input: 217 28% 84%;
    --color-ring: 193 100% 43%;
    --color-cyan: 193 100% 43%;
    --color-cyan-glow: 193 100% 43% / 0.18;
    --color-purple: 247 84% 66%;
    --color-purple-glow: 247 84% 66% / 0.18;
    --color-success: 152 69% 40%;
    --color-danger: 0 90% 55%;
    --shadow-soft-card: 0 12px 24px hsl(220 20% 34% / 0.09);
    --shadow-soft-card-strong: 0 18px 34px hsl(220 20% 32% / 0.12);
  }
}

/* === Base Styles === */
@layer base {
  body {
    margin: 0;
    min-width: 320px;
    min-height: 100vh;
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-family: "Inter", "SF Pro Display", "Segoe UI", system-ui, sans-serif;
    line-height: 1.5;
    font-weight: 400;
    font-synthesis: none;
    text-rendering: optimizeLegibility;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }
}

/* === Utility Classes === */
@layer utilities {
  /* Custom scrollbar */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: hsl(var(--foreground) / 0.1); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: hsl(var(--foreground) / 0.2); }

  /* Touch pan */
  .touch-pan-y { touch-action: pan-y; }

  /* Tabular numbers for financial data */
  .tabular-nums { font-variant-numeric: tabular-nums; }

  /* Truncate text */
  .truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* Glass card */
  .glass-card {
    position: relative;
    overflow: hidden;
    border-radius: var(--radius-lg);
    border: 1px solid hsl(var(--border) / 0.6);
    background: hsl(var(--card) / 0.72);
    backdrop-filter: blur(18px);
    box-shadow: var(--shadow-soft-card);
  }

  /* Glow effects */
  .glow-cyan { box-shadow: 0 0 20px var(--color-cyan-glow); }
  .glow-purple { box-shadow: 0 0 20px var(--color-purple-glow); }

  /* Label text */
  .label-uppercase {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    font-size: 11px;
    font-weight: 700;
    line-height: 1;
    letter-spacing: 0.24em;
    text-transform: uppercase;
    color: hsl(var(--muted-foreground));
  }
}

/* === Keyframes === */
@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

@keyframes slideUp {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes pulseGlow {
  0%, 100% { box-shadow: 0 0 20px var(--color-cyan-glow); }
  50% { box-shadow: 0 0 40px var(--color-cyan-glow); }
}

/* === Markdown Prose === */
.home-markdown-prose :where(h1) { border-bottom: 1px solid hsl(var(--border)); padding-bottom: 0.5rem; }
.home-markdown-prose :where(h2) { color: hsl(var(--color-purple)); }
.home-markdown-prose :where(code) { color: hsl(var(--primary)); background: hsl(var(--primary) / 0.1); border-radius: 0.375rem; padding: 0.125rem 0.375rem; }
.home-markdown-prose :where(pre) { border: 1px solid hsl(var(--border)); background: hsl(var(--muted)); }
.home-markdown-prose :where(th, td) { border: 1px solid hsl(var(--border)); padding: 4px 6px; }
.home-markdown-prose :where(th) { background: hsl(var(--muted)); color: hsl(var(--foreground)); }
.home-markdown-prose :where(blockquote) { border-left: 3px solid hsl(var(--color-purple) / 0.3); background: hsl(var(--color-purple) / 0.08); border-radius: 0 0.75rem 0.75rem 0; padding: 0.5rem 1rem; color: hsl(var(--secondary-foreground)); }
```

- [ ] **Step 2: 精简 tailwind.config.js**

替换 `apps/dsa-web/tailwind.config.js` 内容为 shadcn 最小配置：

```javascript
/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class'],
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    container: {
      center: true,
      padding: '1.5rem',
      screens: { '2xl': '1400px' },
    },
    extend: {
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      colors: {},
      keyframes: {},
      animation: {},
    },
  },
  plugins: [],
};
```

注意：所有颜色、动画、shadow 等现在由 `@theme` 在 CSS 中定义，不再需要 config 中的 extend。

- [ ] **Step 3: 更新 main.tsx CSS import**

修改 `apps/dsa-web/src/main.tsx` 第 3 行：

```typescript
// Before:
import './index.css'
// After:
import './styles/globals.css'
```

- [ ] **Step 4: 创建 styles 目录结构**

```bash
mkdir -p apps/dsa-web/src/styles
```

- [ ] **Step 5: 验证 dev 启动**

```bash
cd apps/dsa-web
npm run dev
```

Expected: Vite 启动成功，页面应该能渲染（但样式可能不完整因为组件还没迁移）

- [ ] **Step 6: Commit**

```bash
git add apps/dsa-web/src/styles/globals.css apps/dsa-web/tailwind.config.js apps/dsa-web/src/main.tsx
git commit -m "refactor: rewrite CSS system with @theme tokens, reduce from 2570 to ~600 lines"
```

---

### Task 3: 修复 ThemeProvider 支持暗色模式

**Files:**
- Modify: `apps/dsa-web/src/components/theme/ThemeProvider.tsx`

- [ ] **Step 1: 重写 ThemeProvider**

替换 `apps/dsa-web/src/components/theme/ThemeProvider.tsx`：

```tsx
import type React from 'react';
import { ThemeProvider as NextThemesProvider } from 'next-themes';

type ThemeProviderProps = {
  children: React.ReactNode;
};

export const ThemeProvider: React.FC<ThemeProviderProps> = ({ children }) => {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem={true}
      disableTransitionOnChange={false}
    >
      {children}
    </NextThemesProvider>
  );
};
```

关键变更：
- `defaultTheme="system"` — 跟随系统偏好
- `enableSystem={true}` — 启用系统检测
- `disableTransitionOnChange={false}` — 切换时启用过渡动画

- [ ] **Step 2: Commit**

```bash
git add apps/dsa-web/src/components/theme/ThemeProvider.tsx
git commit -m "feat: enable system dark mode detection with smooth transitions"
```

---

### Task 4: 创建 ThemeToggle 组件

**Files:**
- Create: `apps/dsa-web/src/components/layout/ThemeToggle.tsx`

- [ ] **Step 1: 创建 ThemeToggle**

```tsx
'use client';

import { Moon, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import { Button } from '@/components/ui/button';

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
      className="h-9 w-9 rounded-xl"
      aria-label="切换主题"
      title={theme === 'dark' ? '切换到亮色' : '切换到暗色'}
    >
      <Sun className="h-4 w-4 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
      <Moon className="absolute h-4 w-4 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
      <span className="sr-only">切换主题</span>
    </Button>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/dsa-web/src/components/layout/ThemeToggle.tsx
git commit -m "feat: add theme toggle component for dark/light mode switching"
```

---

### Task 5: 重写 Shell（侧栏导航 + 动画 + ThemeToggle）

**Files:**
- Modify: `apps/dsa-web/src/components/layout/Shell.tsx`

- [ ] **Step 1: 重写 Shell**

```tsx
import { useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { BarChart3, ChevronLeft, ChevronRight, GitBranch, Home, Search, Settings, Star, Zap } from 'lucide-react';
import { NavLink, Outlet } from 'react-router-dom';
import { cn } from '@/utils/cn';
import { ThemeToggle } from './ThemeToggle';

const navItems = [
  { to: '/', label: '工作台', icon: Home },
  { to: '/stocks', label: '全市场股票', icon: Search },
  { to: '/portfolio', label: '管理自选股', icon: Star },
  { to: '/analysis', label: '个股分析', icon: Zap },
  { to: '/workflows', label: '工作流编排', icon: GitBranch },
  { to: '/settings', label: '模型 API 配置', icon: Settings },
];

export function Shell() {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto flex min-h-screen w-full max-w-[1720px] gap-3 px-3 py-3 sm:px-4 sm:py-4 lg:px-5">
        <motion.aside
          layout
          transition={{ duration: 0.3, ease: 'easeInOut' }}
          className={cn(
            'hidden min-h-0 shrink-0 flex-col rounded-[1.35rem] border border-border/60 bg-card/50 backdrop-blur-xl lg:flex',
            collapsed ? 'w-[4.75rem]' : 'w-64',
          )}
          aria-label="主菜单"
        >
          {/* Logo */}
          <div className="mb-4 flex items-center gap-3 px-3 pt-2">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary shadow-sm">
              <BarChart3 className="h-5 w-5" />
            </div>
            <AnimatePresence mode="wait">
              {!collapsed && (
                <motion.div
                  initial={{ opacity: 0, width: 0 }}
                  animate={{ opacity: 1, width: 'auto' }}
                  exit={{ opacity: 0, width: 0 }}
                  transition={{ duration: 0.2 }}
                  className="min-w-0 overflow-hidden"
                >
                  <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">Stock Signal</p>
                  <p className="truncate text-sm font-semibold text-foreground">Stock-Signal-Desk</p>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Nav */}
          <nav className="flex flex-1 flex-col gap-1 px-2">
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  title={collapsed ? item.label : undefined}
                  className={({ isActive }) =>
                    cn(
                      'flex h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium transition-colors',
                      collapsed && 'justify-center px-0',
                      isActive
                        ? 'bg-primary/10 text-primary'
                        : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                    )
                  }
                >
                  <Icon className="h-[18px] w-[18px] shrink-0" />
                  <AnimatePresence mode="wait">
                    {!collapsed && (
                      <motion.span
                        initial={{ opacity: 0, width: 0 }}
                        animate={{ opacity: 1, width: 'auto' }}
                        exit={{ opacity: 0, width: 0 }}
                        transition={{ duration: 0.15 }}
                        className="truncate"
                      >
                        {item.label}
                      </motion.span>
                    )}
                  </AnimatePresence>
                </NavLink>
              );
            })}
          </nav>

          {/* Collapse toggle + Theme toggle */}
          <div className="mt-3 flex items-center gap-1 px-2">
            <button
              type="button"
              onClick={() => setCollapsed((v) => !v)}
              className="flex h-9 flex-1 items-center justify-center rounded-xl border border-border/60 bg-card/50 text-muted-foreground transition hover:text-primary"
              aria-label={collapsed ? '展开菜单栏' : '收起菜单栏'}
            >
              {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
            </button>
            <AnimatePresence>
              {!collapsed && <ThemeToggle />}
            </AnimatePresence>
          </div>
        </motion.aside>

        {/* Main content */}
        <main className="min-h-0 min-w-0 flex-1 touch-pan-y">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 验证侧栏动画**

```bash
cd apps/dsa-web
npm run dev
```

Expected: 侧栏能正常渲染，收起/展开有平滑动画，底部有 ThemeToggle

- [ ] **Step 3: Commit**

```bash
git add apps/dsa-web/src/components/layout/Shell.tsx
git commit -m "refactor: rewrite Shell with motion layout animations and theme toggle"
```

---

## Phase 2: 动画层 + 路由过渡

### Task 6: 添加路由过渡动画到 App.tsx

**Files:**
- Modify: `apps/dsa-web/src/App.tsx`

- [ ] **Step 1: 重写 App.tsx 添加 AnimatePresence**

```tsx
import type React from 'react';
import { lazy, Suspense } from 'react';
import { BrowserRouter as Router, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AnimatePresence, motion } from 'motion/react';
import { Shell } from '@/components/layout/Shell';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';

const HomePage = lazy(() => import('@/pages/HomePage'));
const BatchRunDetailPage = lazy(() => import('@/pages/BatchRunDetailPage'));
const LoginPage = lazy(() => import('@/pages/LoginPage'));
const NotFoundPage = lazy(() => import('@/pages/NotFoundPage'));
const SettingsPage = lazy(() => import('@/pages/SettingsPage'));
const MarketStocksPage = lazy(() => import('@/pages/MarketStocksPage'));
const WatchlistManagePage = lazy(() => import('@/pages/WatchlistManagePage'));
const WorkflowBuilderPage = lazy(() => import('@/pages/WorkflowBuilderPage'));
const StockAnalysisPage = lazy(() => import('@/pages/StockAnalysisPage'));

// Page fallback loading spinner
const PageFallback = () => (
  <div className="flex min-h-screen items-center justify-center bg-background">
    <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
  </div>
);

// Animated page wrapper
const AnimatedPage = ({ children }: { children: React.ReactNode }) => (
  <motion.div
    initial={{ opacity: 0, y: 16 }}
    animate={{ opacity: 1, y: 0 }}
    exit={{ opacity: 0, y: -16 }}
    transition={{ duration: 0.25, ease: 'easeOut' }}
  >
    {children}
  </motion.div>
);

const AppContent: React.FC = () => {
  const location = useLocation();
  const { authEnabled, loggedIn, isLoading, loadError, refreshStatus } = useAuth();

  if (isLoading) return <PageFallback />;

  if (loadError) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background px-4">
        <div className="w-full max-w-lg">
          {/* TODO: ApiErrorAlert import path will change */}
          <p className="text-center text-danger">{loadError}</p>
        </div>
        <button type="button" className="btn-primary" onClick={() => void refreshStatus()}>重试</button>
      </div>
    );
  }

  if (authEnabled && !loggedIn) {
    if (location.pathname === '/login') {
      return (
        <Suspense fallback={<PageFallback />}>
          <LoginPage />
        </Suspense>
      );
    }
    const redirect = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?redirect=${redirect}`} replace />;
  }

  if (location.pathname === '/login') return <Navigate to="/" replace />;

  return (
    <Suspense fallback={<PageFallback />}>
      <AnimatePresence mode="wait">
        <Routes location={location} key={location.pathname}>
          <Route element={<Shell />}>
            <Route path="/" element={<AnimatedPage><HomePage /></AnimatedPage>} />
            <Route path="/batch/runs/:runId" element={<AnimatedPage><BatchRunDetailPage /></AnimatedPage>} />
            <Route path="/stocks" element={<AnimatedPage><MarketStocksPage /></AnimatedPage>} />
            <Route path="/portfolio" element={<AnimatedPage><WatchlistManagePage /></AnimatedPage>} />
            <Route path="/analysis" element={<AnimatedPage><StockAnalysisPage /></AnimatedPage>} />
            <Route path="/workflows" element={<AnimatedPage><WorkflowBuilderPage /></AnimatedPage>} />
            <Route path="/settings" element={<AnimatedPage><SettingsPage /></AnimatedPage>} />
            <Route path="*" element={<AnimatedPage><NotFoundPage /></AnimatedPage>} />
          </Route>
          <Route path="/login" element={<LoginPage />} />
        </Routes>
      </AnimatePresence>
    </Suspense>
  );
};

const App: React.FC = () => (
  <Router>
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  </Router>
);

export default App;
```

注意：此时 `@/` alias 已经在 Task 1 中配置好。`components/common` 的 import 将在后续 Task 中更新。

- [ ] **Step 2: 验证路由动画**

```bash
cd apps/dsa-web
npm run dev
```

Expected: 页面切换时有淡入上滑动画

- [ ] **Step 3: Commit**

```bash
git add apps/dsa-web/src/App.tsx
git commit -m "feat: add motion AnimatePresence route transition animations"
```

---

## Phase 3: 页面逐页重构

### Task 7: 拆分 HomePage（672行 → 8个文件）

**Files:**
- Create: `apps/dsa-web/src/pages/HomePage/index.tsx`
- Create: `apps/dsa-web/src/pages/HomePage/SearchPanel.tsx`
- Create: `apps/dsa-web/src/pages/HomePage/ReportArea.tsx`
- Create: `apps/dsa-web/src/pages/HomePage/TaskPanel.tsx`
- Create: `apps/dsa-web/src/pages/HomePage/HistoryList.tsx`
- Create: `apps/dsa-web/src/pages/HomePage/WorkflowGuide.tsx`
- Create: `apps/dsa-web/src/pages/HomePage/useHomeActions.ts`
- Create: `apps/dsa-web/src/pages/HomePage/useTaskManager.ts`
- Delete: `apps/dsa-web/src/pages/HomePage.tsx`

HomePage 拆分策略：
- `index.tsx` — 布局编排，组合子组件
- `SearchPanel.tsx` — 搜索框 + 模板选择 + 分析按钮
- `ReportArea.tsx` — 报告/对话展示区域
- `TaskPanel.tsx` — 实时任务面板
- `HistoryList.tsx` — 历史记录列表
- `WorkflowGuide.tsx` — 右侧工作流指引
- `useHomeActions.ts` — 分析提交、历史管理等业务逻辑
- `useTaskManager.ts` — 任务状态管理

由于这是最大最复杂的页面拆分，实现时将直接从现有 `HomePage.tsx` 中提取逻辑到子组件，保持所有功能不变，只是重组代码结构。子组件使用 shadcn/ui 的 Card、Skeleton 等替代原有组件。

- [ ] **Step 1: 创建 HomePage 子组件和 hooks**

从 `HomePage.tsx` 中提取以下组件：
- `SearchPanel` — 包含 StockAutocomplete、模板 select、分析按钮、通知开关
- `ReportArea` — 包含报告展示、ConversationReport、loading/empty 状态
- `TaskPanel` — 任务列表展示
- `HistoryList` — 历史记录列表 + 多选 + 删除
- `WorkflowGuide` — 右侧使用指南

Hooks：
- `useHomeActions` — 封装 submitAnalysis、deleteSelectedHistory、handleReanalyze
- `useTaskManager` — 封装任务点击、状态加载、报告预览逻辑

每个文件不超过 100 行（hooks 不超过 150 行）。

- [ ] **Step 2: 更新路由指向新目录**

修改 `src/App.tsx` 中 HomePage 的 import：

```tsx
// Before:
const HomePage = lazy(() => import('./pages/HomePage'));
// After:
const HomePage = lazy(() => import('./pages/HomePage'));
// (same path, HomePage/index.tsx will be the entry point)
```

- [ ] **Step 3: 验证功能**

```bash
cd apps/dsa-web
npm run dev
```

Expected: 工作台功能完全正常，所有子组件渲染正确

- [ ] **Step 4: 删除旧文件**

```bash
rm apps/dsa-web/src/pages/HomePage.tsx
```

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/pages/HomePage/ apps/dsa-web/src/pages/HomePage.tsx
git commit -m "refactor: split HomePage (672 lines) into 8 focused files"
```

---

### Task 8: 重构 MarketStocksPage

**Files:**
- Modify: `apps/dsa-web/src/pages/MarketStocksPage.tsx`

- [ ] **Step 1: 替换为 shadcn 组件 + 动画**

关键改动：
1. `InlineAlert` → shadcn `Alert`
2. 同步状态卡片 → 带脉冲光效的实时指示器
3. 股票卡片 → motion stagger 列表（`motion.div` with `variants` + `staggerChildren`）
4. 加载状态 → shadcn `Skeleton` 替代 spinner
5. Plus 按钮 → shadcn `DropdownMenu`（添加/快速分析）
6. 搜索框 + 筛选栏合并一行

代码使用 Tailwind utility classes 替代原有的 CSS 变量引用。

- [ ] **Step 2: 验证**

```bash
cd apps/dsa-web
npm run dev
```

Expected: 全市场页面正常渲染，股票卡片 stagger 动画出现

- [ ] **Step 3: Commit**

```bash
git add apps/dsa-web/src/pages/MarketStocksPage.tsx
git commit -m "refactor: MarketStocksPage with shadcn components and stagger animations"
```

---

### Task 9: 重构 WatchlistManagePage

**Files:**
- Modify: `apps/dsa-web/src/pages/WatchlistManagePage.tsx`

- [ ] **Step 1: 替换为 shadcn Tabs + Sheet + Popover**

关键改动：
1. 分组 tab → shadcn `Tabs`（带滑动指示器）
2. Drawer → shadcn `Sheet`
3. 搜索建议 → shadcn `Popover`
4. 批量操作动画 → 卡片移除时飞走效果
5. `InlineAlert` → shadcn `Alert`

- [ ] **Step 2: 验证**

```bash
cd apps/dsa-web
npm run dev
```

Expected: 自选股管理页面正常，Tabs 切换流畅

- [ ] **Step 3: Commit**

```bash
git add apps/dsa-web/src/pages/WatchlistManagePage.tsx
git commit -m "refactor: WatchlistManagePage with shadcn Tabs, Sheet, and Popover"
```

---

### Task 10: 重构 StockAnalysisPage（大改）

**Files:**
- Modify: `apps/dsa-web/src/pages/StockAnalysisPage.tsx`

- [ ] **Step 1: 升级行情卡片 + 添加图表**

关键改动：
1. 行情价格卡片 → 霓虹光效 + 渐变边框（`shadow-[0_0_30px_hsl(var(--cyan-glow))]`）
2. 涨跌幅数字 → motion animate 计数（从旧值滚动到新值）
3. 增加 recharts 迷你趋势图（显示 52w high/low 范围条）
4. Dimension select → shadcn `Tabs`（实时/历史/技术）
5. 空状态 → 优化搜索引导
6. 数据 stagger 出现

- [ ] **Step 2: 验证**

```bash
cd apps/dsa-web
npm run dev
```

Expected: 个股分析页面正常，行情数据以动画出现

- [ ] **Step 3: Commit**

```bash
git add apps/dsa-web/src/pages/StockAnalysisPage.tsx
git commit -m "refactor: StockAnalysisPage with neon quote cards, animated numbers, and recharts"
```

---

### Task 11: 重构 SettingsPage + WorkflowBuilderPage + BatchRunDetailPage

**Files:**
- Modify: `apps/dsa-web/src/pages/SettingsPage.tsx`
- Modify: `apps/dsa-web/src/pages/WorkflowBuilderPage.tsx`
- Modify: `apps/dsa-web/src/pages/BatchRunDetailPage.tsx`

- [ ] **Step 1: SettingsPage**

- `InlineAlert` → shadcn `Alert`
- `Button` → shadcn `Button`
- 表单 Input → shadcn `Input`
- 添加 shadcn `Tabs` 分配置区域
- 加载状态 → shadcn `Skeleton`

- [ ] **Step 2: WorkflowBuilderPage**

- 拖拽排序 → motion `drag` + `dragConstraints`
- `InlineAlert` → shadcn `Alert`
- `Button` → shadcn `Button`
- 节点卡片 → shadcn `Card`

- [ ] **Step 3: BatchRunDetailPage**

- `Button` → shadcn `Button`
- `ApiErrorAlert` → 保留或简化为 shadcn `Alert`
- `EmptyState` → 保留（无对应 shadcn 组件）
- 状态 badge → shadcn `Badge`
- 进度条动画

- [ ] **Step 4: 验证所有页面**

```bash
cd apps/dsa-web
npm run dev
```

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/pages/SettingsPage.tsx apps/dsa-web/src/pages/WorkflowBuilderPage.tsx apps/dsa-web/src/pages/BatchRunDetailPage.tsx
git commit -m "refactor: Settings, Workflow, Batch pages with shadcn components"
```

---

### Task 12: 更新 LoginPage + NotFoundPage

**Files:**
- Modify: `apps/dsa-web/src/pages/LoginPage.tsx`
- Modify: `apps/dsa-web/src/pages/NotFoundPage.tsx`

- [ ] **Step 1: LoginPage**

- 更新 import：`Button`、`InlineAlert`、`Input` 从 `@/components/ui/` 导入
- 保持现有 motion 3D 效果（已经很酷）
- 更新 CSS 变量引用：`var(--login-*)` 替换为 Tailwind 类或新的 `@theme` tokens

- [ ] **Step 2: NotFoundPage**

- 更新 import
- 替换内联 gradient 为 Tailwind 类
- 添加 motion 动画

- [ ] **Step 3: Commit**

```bash
git add apps/dsa-web/src/pages/LoginPage.tsx apps/dsa-web/src/pages/NotFoundPage.tsx
git commit -m "refactor: update LoginPage and NotFoundPage imports and styles"
```

---

## Phase 4: 清理

### Task 13: 删除旧组件 + 更新 common/index.ts

**Files:**
- Delete: `apps/dsa-web/src/components/common/Button.tsx`
- Delete: `apps/dsa-web/src/components/common/Card.tsx`
- Delete: `apps/dsa-web/src/components/common/Drawer.tsx`
- Delete: `apps/dsa-web/src/components/common/ConfirmDialog.tsx`
- Delete: `apps/dsa-web/src/components/common/Select.tsx`
- Delete: `apps/dsa-web/src/components/common/Input.tsx`
- Delete: `apps/dsa-web/src/components/common/Badge.tsx`
- Delete: `apps/dsa-web/src/components/common/Tooltip.tsx`
- Delete: `apps/dsa-web/src/components/common/InlineAlert.tsx`
- Delete: `apps/dsa-web/src/components/common/Loading.tsx`
- Delete: `apps/dsa-web/src/components/common/ScrollArea.tsx`
- Delete: `apps/dsa-web/src/components/common/Toolbar.tsx`
- Delete: `apps/dsa-web/src/App.css`
- Delete: `apps/dsa-web/src/index.css`
- Modify: `apps/dsa-web/src/components/common/index.ts`

- [ ] **Step 1: 更新 common/index.ts**

只保留未被替代的组件：

```typescript
export * from './EmptyState';
export * from './ApiErrorAlert';
export * from './ScoreGauge';
export * from './StatusDot';
export * from './EyeToggleIcon';
export * from './ParticleBackground';
```

- [ ] **Step 2: 搜索并更新所有残留引用**

```bash
cd apps/dsa-web
grep -r "from.*components/common" src/ --include="*.tsx" --include="*.ts" -l
```

对每个文件，更新 import：
- `Button` → `@/components/ui/button`
- `Card` → `@/components/ui/card`
- `Drawer` → `@/components/ui/sheet`
- 等等

- [ ] **Step 3: 删除旧文件**

```bash
cd apps/dsa-web/src/components/common
rm Button.tsx Card.tsx Drawer.tsx ConfirmDialog.tsx Select.tsx Input.tsx Badge.tsx Tooltip.tsx InlineAlert.tsx Loading.tsx ScrollArea.tsx Toolbar.tsx
rm ../../App.css
rm ../../index.css
```

- [ ] **Step 4: 验证 build**

```bash
cd apps/dsa-web
npx tsc --noEmit
npm run build
```

Expected: 无类型错误，build 成功

- [ ] **Step 5: Commit**

```bash
git add -A apps/dsa-web/src/components/common/ apps/dsa-web/src/App.css apps/dsa-web/src/index.css
git commit -m "chore: delete obsolete common components and App.css, update imports"
```

---

## Phase 5: 测试重写

### Task 14: 重写组件测试

**Files:**
- Delete: `apps/dsa-web/src/components/common/__tests__/Button.test.tsx`
- Delete: `apps/dsa-web/src/components/common/__tests__/Input.test.tsx`
- Delete: `apps/dsa-web/src/components/common/__tests__/ScrollArea.test.tsx`
- Create: `apps/dsa-web/src/components/ui/__tests__/button.test.tsx`
- Create: `apps/dsa-web/src/components/ui/__tests__/input.test.tsx`
- Create: `apps/dsa-web/src/components/features/__tests__/stock-autocomplete.test.tsx`
- Modify: 其他现有测试文件

- [ ] **Step 1: 删除旧测试文件**

```bash
cd apps/dsa-web
rm src/components/common/__tests__/Button.test.tsx
rm src/components/common/__tests__/Input.test.tsx
rm src/components/common/__tests__/ScrollArea.test.tsx
```

- [ ] **Step 2: 创建新测试文件**

shadcn/ui 组件本身已经经过充分测试，不需要重新测试基础渲染。测试重点放在业务封装上：

```typescript
// src/components/features/__tests__/stock-autocomplete.test.tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { StockAutocomplete } from '../stock-autocomplete';

describe('StockAutocomplete', () => {
  it('renders input with placeholder', () => {
    render(<StockAutocomplete placeholder="搜索..." value="" onChange={() => {}} onSubmit={() => {}} />);
    expect(screen.getByPlaceholderText('搜索...')).toBeInTheDocument();
  });

  it('calls onSubmit when Enter is pressed with value', async () => {
    const handleSubmit = vi.fn();
    render(<StockAutocomplete value="600519" onChange={() => {}} onSubmit={handleSubmit} />);
    const input = screen.getByRole('textbox');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(handleSubmit).toHaveBeenCalled());
  });
});
```

- [ ] **Step 3: 运行测试**

```bash
cd apps/dsa-web
npm run test
```

Expected: 所有测试通过

- [ ] **Step 4: Commit**

```bash
git add apps/dsa-web/src/components/
git commit -m "test: rewrite component tests for shadcn/ui architecture"
```

---

### Task 15: 重写页面测试 + E2E

**Files:**
- Modify: `apps/dsa-web/src/pages/__tests__/HomePage.test.tsx`
- Modify: `apps/dsa-web/src/pages/__tests__/LoginPage.test.tsx`
- Modify: `apps/dsa-web/e2e/smoke.spec.ts`
- Modify: `apps/dsa-web/e2e/report-markdown.spec.ts`

- [ ] **Step 1: 更新页面测试**

更新测试 import 路径，适配新的组件架构。保持测试重点：
- HomePage: 渲染、搜索、历史选择
- LoginPage: 渲染、表单验证

- [ ] **Step 2: 更新 E2E 测试**

检查 `e2e/smoke.spec.ts` 和 `e2e/report-markdown.spec.ts`，更新选择器以匹配新的 DOM 结构（shadcn/ui 组件的 data-testid 和 class 名可能不同）。

- [ ] **Step 3: 运行全部测试**

```bash
cd apps/dsa-web
npm run test
npx playwright test
```

- [ ] **Step 4: 最终 build 验证**

```bash
cd apps/dsa-web
npm run build
```

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/pages/__tests__/ apps/dsa-web/e2e/
git commit -m "test: rewrite page and E2E tests for refactored architecture"
```

---

## 验收检查清单

完成所有 Task 后，逐项验证：

- [ ] 所有 7 个页面正常渲染（`npm run dev` 手动浏览）
- [ ] 暗色/亮色模式切换正常（侧栏底部 ThemeToggle）
- [ ] 路由过渡动画流畅（页面切换淡入上滑 0.25s）
- [ ] 所有 shadcn/ui 组件正常工作（button, card, dialog, alert-dialog, select, input, badge, tooltip, alert, skeleton, scroll-area, tabs, dropdown-menu, popover, separator, sheet）
- [ ] CSS 从 2570 行减少到 ~600 行（`wc -l src/styles/globals.css`）
- [ ] 页面组件文件不超过 100 行（hook 文件不超过 150 行）
- [ ] 所有测试通过（`npm run test` + `npx playwright test`）
- [ ] 无 TypeScript 类型错误（`npx tsc --noEmit`）
- [ ] npm run build 成功
- [ ] 无 console error（浏览器 DevTools Console 检查）
