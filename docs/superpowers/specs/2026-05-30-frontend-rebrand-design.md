# 前端全面重构设计文档

> 日期: 2026-05-30
> 状态: 待审阅
> 范围: apps/dsa-web/ 全量前端重构

## 1. 目标

- **酷炫**: 金融终端风格视觉设计，霓虹色数据可视化，渐变光效
- **好用**: shadcn/ui 统一组件，一致的交互模式，完善的空状态/错误处理
- **丝滑**: 全面引入 motion 动画 — 路由过渡、stagger 列表、数字滚动、骨架屏、hover 微交互
- **整洁**: 删除无用代码，2570 行 CSS → ~600 行，单文件 ≤100 行

## 2. 技术栈

| 分类 | 包 | 用途 |
|------|-----|------|
| UI | shadcn/ui | Button, Card, Dialog, Sheet, Select, Input, Badge, Tooltip, Alert, Skeleton, ScrollArea, Tabs, DropdownMenu, Popover, Separator |
| 动画 | motion | 页面过渡、路由动画、卡片入场、数字滚动、layout 动画 |
| 主题 | next-themes | 暗色模式切换，默认跟随系统 |
| 状态 | zustand | 全局状态（保留现有） |
| 图表 | recharts | 数据可视化（保留现有） |
| 样式 | Tailwind v4 | 从 2570 行 CSS → ~600 行，@theme 定义设计 token |
| 测试 | vitest + playwright | 全部重写 |

## 3. 目录结构

```
apps/dsa-web/src/
├── api/                     ← 不变 (API 层)
├── components/
│   ├── ui/                  ← shadcn/ui 自动生成 (12+ 组件)
│   ├── features/            ← 业务组件 (替代 common/)
│   │   ├── stock-autocomplete/
│   │   ├── report/
│   │   ├── batch/
│   │   └── template/
│   └── layout/              ← Shell, PageHeader, ThemeToggle
├── hooks/                   ← 增强 hooks
├── pages/                   ← 精简后的页面 (每个 ≤100行)
│   ├── HomePage/            ← 672行拆为 8 个文件
│   ├── MarketStocksPage/
│   ├── WatchlistManagePage/
│   ├── StockAnalysisPage/
│   ├── SettingsPage/
│   ├── WorkflowBuilderPage/
│   ├── BatchRunDetailPage/
│   ├── LoginPage/
│   └── NotFoundPage/
├── stores/                  ← zustand stores (保留优化)
├── contexts/                ← AuthContext (保留)
├── styles/
│   ├── globals.css          ← @theme + base (~400行)
│   └── animations.css       ← motion 关键帧 (~200行)
├── utils/                   ← 工具函数 (保留)
├── types/                   ← TypeScript 类型 (保留)
├── App.tsx                  ← 路由 + AnimatePresence
└── main.tsx                 ← 入口
```

## 4. Design System

### 4.1 CSS 精简

**现状**: 2570 行 CSS，140+ 个变量（:root + .dark 各一套），大量重复命名

**目标**: ~600 行，30+ 核心 @theme token，Tailwind 原生 utility 优先

### 4.2 Design Tokens

```css
@theme {
  --color-background: 228 35% 7%;        /* 暗色背景 */
  --color-foreground: 210 33% 98%;        /* 暗色文字 */
  --color-primary: 190 100% 50%;          /* Cyan 主色 */
  --color-cyan: 190 100% 50%;
  --color-purple: 247 84% 72%;            /* 紫色点缀 */
  --color-success: 152 69% 40%;           /* 绿色/A股跌 */
  --color-danger: 0 90% 55%;              /* 红色/A股涨 */
  --color-warning: 37 92% 50%;            /* 琥珀色警告 */
  --color-card: 230 24% 10%;              /* 卡片背景 */
  --color-muted: 230 18% 14%;             /* 弱化背景 */
  --color-border: 226 19% 20%;            /* 边框 */
  --radius-lg: 1rem;
  --radius-xl: 1.35rem;
  --shadow-soft-card: 0 18px 48px hsl(215 25% 10% / 0.4);
  --animate-fade-in: fadeIn 0.3s ease-out;
  --animate-slide-up: slideUp 0.4s ease-out;
}
```

### 4.3 暗色模式

- 默认跟随系统偏好（next-themes 自动检测 prefers-color-scheme）
- 侧栏底部 Toggle 按钮支持手动切换
- 视觉设计在暗色模式下效果最佳（深色背景 + 霓虹光效），但浅色模式同样完整支持
- CSS 变量通过 Tailwind v4 @media (prefers-color-scheme: dark) 自动切换

### 4.4 色彩规范（金融终端）

| 颜色 | HSL | 语义 |
|------|-----|------|
| Cyan | 190 100% 50% | Primary / 数据主色 / 光效 |
| Purple | 247 84% 72% | Accent / 标签色 / 分类 |
| Red | 0 90% 55% | 涨 / 危险 / 删除 (A股) |
| Green | 149 100% 42% | 跌 / 安全 / 成功 (A股) |
| Amber | 37 92% 50% | Warning / 中性提示 |

## 5. 动画系统

### 5.1 五层动画体系

| 层级 | 触发场景 | 动画效果 | 时长 |
|------|----------|----------|------|
| 页面级 | 路由切换 | 淡入 + 上滑 | 0.25s ease-out |
| 布局级 | 侧栏展开/收起 | layout 动画 | 0.3s ease |
| 组件级 | 卡片出现/删除 | stagger 列表 | 0.05s 间隔 |
| 数据级 | 数字变化 | 数字滚动 + 颜色渐变 | 0.5s ease |
| 反馈级 | 按钮点击 | 缩放 + 颜色脉冲 | 0.15s ease |

### 5.2 路由过渡

```tsx
import { AnimatePresence, motion } from "motion/react";

<AnimatePresence mode="wait">
  <motion.div
    key={location.pathname}
    initial={{ opacity: 0, y: 16 }}
    animate={{ opacity: 1, y: 0 }}
    exit={{ opacity: 0, y: -16 }}
    transition={{ duration: 0.25 }}
  >
    <Routes location={location}>...</Routes>
  </motion.div>
</AnimatePresence>
```

### 5.3 关键动画清单

1. **列表 Stagger 入场** — 股票/历史卡片依次出现，间隔 0.05s
2. **骨架屏呼吸** — 加载时用 Skeleton + pulse 动画替代 spinner
3. **数字滚动** — 股价/评分/百分比平滑过渡
4. **卡片 Hover 微交互** — 上浮 2px + 阴影加深 + 边框发光
5. **任务状态脉冲** — AI 分析中卡片的光点脉冲 + 进度条动画

## 6. 逐页改造

### 6.1 HomePage（工作台，672行 → 8个文件）

**拆分后结构**:
```
pages/HomePage/
├── index.tsx              ← 布局编排 (~80行)
├── SearchPanel.tsx        ← 搜索/模板/分析按钮 (~60行)
├── ReportArea.tsx         ← 报告/对话展示 (~50行)
├── TaskPanel.tsx          ← 任务面板 (~40行)
├── HistoryList.tsx        ← 历史记录列表 (~50行)
├── WorkflowGuide.tsx      ← 右侧工作流指引 (~30行)
├── useHomeActions.ts      ← 业务逻辑 hook (~100行)
└── useTaskManager.ts      ← 任务管理 hook (~60行)
```

**改动**:
- 三栏布局增强（保持现有布局方向）
- 报告区加载骨架屏替代 loading block
- 任务列表 stagger 动画
- 历史记录卡片 hover 微交互
- 统一 ConfirmDialog 为 shadcn/ui alert-dialog
- 统一 Drawer 为 shadcn/ui sheet

### 6.2 Shell（侧栏导航，重写）

**改动**:
- shadcn/ui Tooltip 替代原生 title
- motion layout 动画替代 CSS transition（侧栏收起/展开）
- 底部添加 Theme Toggle
- Logo 区域品牌化
- 收起/展开时图标和文字 stagger 过渡

### 6.3 MarketStocksPage（全市场股票）

**改动**:
- 股票卡片 stagger 列表动画
- shadcn/ui Skeleton 替代 spinner
- shadcn/ui DropdownMenu 替代 Plus 按钮（添加/快速分析）
- 同步状态卡片改为实时光效指示器
- 搜索框 + 筛选栏合并一行
- 无限滚动加载动画

### 6.4 WatchlistManagePage（自选股管理）

**改动**:
- shadcn/ui Tabs 替代手写分组 tab（带滑动指示器动画）
- shadcn/ui Sheet 替代 Drawer
- 搜索建议改为 Popover
- 批量操作动画（卡片逐个移除飞走效果）
- 空状态优化

### 6.5 StockAnalysisPage（个股分析，大改）

**改动**:
- 实时行情卡片全面升级：霓虹光效 + 渐变边框
- 涨跌幅数字带动画计数
- 增加 recharts 迷你趋势图
- Dimension 改为 shadcn/ui Tabs（实时/历史/技术）
- 空状态搜索引导
- 选择股票后行情数据 stagger 出现

### 6.6 SettingsPage / WorkflowBuilderPage / BatchRunDetailPage

**共同改动**:
- 所有表单 Input/Select 替换为 shadcn/ui
- ConfirmDialog 统一为 alert-dialog
- 页面过渡动画

**单独改动**:
- SettingsPage: shadcn/ui Tabs 分配置区域
- WorkflowBuilderPage: motion drag 排序
- BatchRunDetailPage: 进度条动画，Badge 状态

## 7. 删除清单

### 7.1 被 shadcn/ui 替代的文件

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
```

### 7.2 被精简的文件

```
src/App.css          ← 完全删除
src/index.css        ← 精简重写 (~600行)
src/components/common/index.ts  ← 更新导出
```

### 7.3 保留的组件

```
src/components/common/EmptyState.tsx
src/components/common/ParticleBackground.tsx
src/components/common/ScoreGauge.tsx
src/components/common/StatusDot.tsx
src/components/common/EyeToggleIcon.tsx
```

## 8. 安装步骤

```bash
# 1. 添加 path alias 到 vite.config.ts
# resolve: { alias: { "@": path.resolve(__dirname, "./src") } }

# 2. 初始化 shadcn/ui
npx shadcn@latest init

# 3. 安装组件
npx shadcn@latest add button card dialog alert-dialog select \
  input badge tooltip alert skeleton scroll-area tabs \
  dropdown-menu popover separator sheet

# 4. 安装 motion (已安装，确认版本)
npm install motion
```

## 9. 测试策略

- 所有现有测试文件重写
- 组件测试覆盖 shadcn/ui 业务封装组件
- 页面测试覆盖交互流程
- Playwright E2E smoke 测试更新

## 10. 验收标准

- [ ] 所有 7 个页面正常渲染
- [ ] 暗色/亮色模式切换正常
- [ ] 路由过渡动画流畅
- [ ] 所有 shadcn/ui 组件正常工作
- [ ] CSS 从 2570 行减少到 ~600 行
- [ ] 页面组件文件不超过 100 行（hook 文件不超过 150 行）
- [ ] 所有测试通过
- [ ] 无 TypeScript 类型错误
- [ ] npm run build 成功
- [ ] 无 console error
