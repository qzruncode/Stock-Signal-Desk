# 催化分析 (Catalyst Analysis) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "催化分析" card to the business analysis tab that evaluates whether a stock has meaningful catalysts in the next 6–12 months, using a new LLM analysis phase in the existing SSE stream.

**Architecture:** Follow the established pattern from environment analysis and track quality — a new LLM call phase appended to the SSE stream (`track_analysis_done` → `catalyst_analysis_start` → chunks → `catalyst_analysis_done`). The LLM receives all already-fetched data (events, forecasts, financials, environment, track quality) plus no new data fetching. The frontend adds a new streaming state, a new component (`CatalystCard`), and updates card ordering.

**Tech Stack:** Python (backend SSE + LLM), TypeScript/React (frontend), existing `call_ai_structured` pattern, existing `useBusinessStream` hook.

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `apps/dsa-web/src/api/business.ts` | Add `CatalystItem`, `CatalystAnalysis` types; extend `BusinessResponse` |
| Modify | `apps/dsa-web/src/hooks/useBusinessStream.ts` | Add `catalystStreamingText` state + SSE event listeners |
| Create | `apps/dsa-web/src/components/CatalystCard.tsx` | Catalyst analysis display component |
| Modify | `apps/dsa-web/src/pages/StockAnalysisPage.tsx` | Import CatalystCard, update card order, add catalyst streaming phase |
| Modify | `api/v1/endpoints/stock_info.py` | Add `_build_catalyst_prompt()`, `_parse_catalyst_analysis()`, SSE phase, cache path |
| Modify | `src/agent/tool_registry.py` | Update `get_stock_business` description with item #9 |

---

### Task 1: Backend — Build Catalyst Prompt

**Files:**
- Modify: `api/v1/endpoints/stock_info.py` (insert after `_build_track_quality_prompt` ~line 934)

- [ ] **Step 1: Add `_build_catalyst_prompt()` function**

Insert after the `_parse_track_quality_analysis` function (after line 952). This function builds the LLM prompt for catalyst analysis using data already fetched during the SSE stream.

```python
def _build_catalyst_prompt(
    symbol: str,
    intro: dict,
    profit_forecast: list[dict],
    financial_summary: dict,
    events: dict,
    environment_analysis: dict | None,
    track_quality: dict | None,
) -> tuple[str, str, str]:
    """Build system + user prompt for LLM catalyst analysis.

    Returns (system_prompt, user_prompt, llm_input_text).
    """
    announcements_text = '\n'.join(
        f"  - [{a['date']}] [{a['type']}] {a['title']}"
        for a in events.get('announcements', [])[:15]
    ) or '暂无公告'
    news_text = '\n'.join(
        f"  - [{n['time']}] [{n['source']}] {n['title']}"
        for n in events.get('news', [])[:10]
    ) or '暂无新闻'

    forecast_text = '\n'.join(
        f"  - {f['analyst']}({f['researcher']}): "
        f"2026E EPS={f['eps_2026']}, 2027E={f['eps_2027']}, 2028E={f['eps_2028']}"
        for f in profit_forecast[:8]
    ) if profit_forecast else '暂无机构预测数据'

    growth_text = _build_growth_text(financial_summary)

    # Extract environment summary
    env_summary = ''
    if environment_analysis and environment_analysis.get('llm_used'):
        dims = []
        for key, label in [('policy', '政策'), ('technology', '技术'), ('demand', '需求'), ('supply_competition', '供给')]:
            dim = environment_analysis.get(key, {})
            if dim.get('signal'):
                dims.append(f"{label}: {dim['signal']}")
        if dims:
            env_summary = '、'.join(dims)
        if environment_analysis.get('overall_verdict'):
            env_summary += f"；综合: {environment_analysis['overall_verdict']}"

    # Extract track quality summary
    track_summary = ''
    if track_quality and track_quality.get('llm_used'):
        parts = []
        for key, label in [('cycle_position', '周期'), ('growth_potential', '空间'), ('competition_intensity', '竞争')]:
            dim = track_quality.get(key, {})
            if dim.get('verdict'):
                parts.append(f"{label}={dim['verdict']}")
        if parts:
            track_summary = '、'.join(parts)
        if track_quality.get('overall_verdict'):
            track_summary += f"；综合: {track_quality['overall_verdict']}"

    system_prompt = """你是一个资深A股策略分析师，擅长判断个股未来 6-12 个月的催化剂。

催化剂是指能够驱动股价出现趋势性行情的具体事件或条件变化，包括但不限于：
- 业绩催化：财报超预期、业绩预告、盈利拐点
- 政策催化：产业政策落地、补贴发放、监管放松
- 事件催化：重大合同、产品发布、并购重组、股权激励
- 行业催化：行业景气度上行、供需拐点、技术突破
- 资金催化：纳入指数、大股东增持、回购计划

请严格基于提供的信息分析，不要编造不存在的事件。对于推断性催化，需标注置信度。

输出格式为 JSON（不要包含 markdown 代码块标记）：
{
  "overall_assessment": "催化充分/催化一般/催化不足",
  "summary": "一句话概括未来6-12个月催化情况（不超过40字）",
  "catalysts": [
    {
      "type": "业绩催化/政策催化/事件催化/行业催化/资金催化",
      "description": "具体描述催化事件",
      "timeframe": "预计触发时间范围，如 2026Q3、2026年下半年",
      "confidence": "高/中/低",
      "impact": "重大/中等/有限"
    }
  ],
  "key_dates": ["需要关注的关键日期或时间窗口"],
  "risks": ["催化可能落空的风险点"]
}"""

    user_prompt = f"""请分析 {symbol} 未来 6-12 个月的催化剂情况。

【公司基本面】
- 主营业务：{intro.get('main_business', '未知')}
- 产品类型：{intro.get('product_type', '未知')}

【机构盈利预测】
{forecast_text}

【财务增长趋势】
{growth_text}

【近期公告】
{announcements_text}

【近期新闻】
{news_text}

【外部环境评估】
{env_summary or '暂无外部环境分析'}

【赛道质量评估】
{track_summary or '暂无赛道质量分析'}

请基于以上信息，判断未来 6-12 个月该公司是否有足够的催化剂驱动股价表现。
重点回答：
1. 有哪些具体的催化事件可以期待？
2. 这些催化的时间窗口和确定性如何？
3. 催化落空的主要风险是什么？"""

    return system_prompt, user_prompt, format_llm_input(system_prompt, user_prompt)
```

- [ ] **Step 2: Add `_parse_catalyst_analysis()` function**

Insert immediately after `_build_catalyst_prompt`.

```python
def _parse_catalyst_analysis(response_text: str, model_used: str, llm_input: str) -> dict:
    """Parse LLM JSON response for catalyst analysis."""
    base = {'llm_used': True, 'model': model_used, 'llm_input': llm_input}

    cleaned = response_text.strip()
    if cleaned.startswith('```'):
        lines = cleaned.split('\n')
        if lines[0].startswith('```') and lines[-1].strip() == '```':
            cleaned = '\n'.join(lines[1:-1])

    try:
        parsed = json.loads(cleaned)
        # Ensure catalysts is a list
        if 'catalysts' not in parsed or not isinstance(parsed['catalysts'], list):
            parsed['catalysts'] = []
        if 'key_dates' not in parsed or not isinstance(parsed['key_dates'], list):
            parsed['key_dates'] = []
        if 'risks' not in parsed or not isinstance(parsed['risks'], list):
            parsed['risks'] = []
        return {**base, **parsed}
    except json.JSONDecodeError:
        logger.warning("[Catalyst] JSON parse failed, falling back to raw text")
        return {**base, 'raw_text': response_text}
```

- [ ] **Step 3: Verify Python syntax**

Run: `python3 -m compileall -q api/`
Expected: No output (no errors)

- [ ] **Step 4: Commit**

```bash
git add api/v1/endpoints/stock_info.py
git commit -m "feat: add catalyst analysis prompt builder and parser"
```

---

### Task 2: Backend — Add Catalyst SSE Phase + Cache Path

**Files:**
- Modify: `api/v1/endpoints/stock_info.py` (SSE stream function + cache path)

- [ ] **Step 1: Add catalyst phase to SSE stream**

In the SSE stream handler (the `generate()` function inside `stream_business_analysis`), find the block after `_enqueue("track_analysis_done", data)` (around line 1429). Insert the catalyst analysis phase after it, before the final `yield` / end of the generator.

Add after the track analysis block (after the `_enqueue("track_analysis_done", data)` line):

```python
                # --- Catalyst analysis ---
                try:
                    _enqueue("catalyst_analysis_start", {})

                    cat_sys, cat_usr, cat_input = _build_catalyst_prompt(
                        symbol, intro, profit_forecast, financial_summary, events,
                        data.get('environment_analysis'), data.get('track_quality'),
                    )

                    def _on_catalyst_text(delta: str, full_text: str):
                        _enqueue("catalyst_analysis_chunk", {"text": delta})

                    cat_response, cat_model, _cat_usage = call_ai_structured(
                        analyzer,
                        system_prompt=cat_sys,
                        user_prompt=cat_usr,
                        call_type="catalyst_analysis",
                        temperature=0.3,
                        max_tokens=2048,
                        response_validator=lambda _text: None,
                        stream=True,
                        stream_text_callback=_on_catalyst_text,
                    )

                    catalyst_analysis = _parse_catalyst_analysis(cat_response, cat_model, cat_input)
                    data['catalyst_analysis'] = catalyst_analysis

                except Exception as e:
                    data['catalyst_analysis'] = {'llm_used': False, 'error': str(e)}

                data = _sanitize(data)
                _business_cache_put(symbol, data)
                _enqueue("catalyst_analysis_done", data)
```

- [ ] **Step 2: Update cache read path to emit catalyst events**

In the cache hit path of the SSE stream (around lines 1218–1257), find the track analysis cache replay block:

```python
            _enqueue("track_analysis_start", {})
            _enqueue("track_analysis_chunk", {"text": track_text})
            _enqueue("track_analysis_done", data)
```

Insert after it:

```python
            # Catalyst analysis from cache
            catalyst = data.get('catalyst_analysis', {})
            if catalyst.get('llm_used'):
                catalyst_text = catalyst.get('raw_text', '')
                if not catalyst_text:
                    # Reconstruct from JSON fields
                    parts = []
                    if catalyst.get('summary'):
                        parts.append(f"**{catalyst['overall_assessment']}** — {catalyst['summary']}")
                    for cat in catalyst.get('catalysts', []):
                        parts.append(f"- [{cat.get('type', '')}] {cat.get('description', '')}（{cat.get('timeframe', '')}，置信度: {cat.get('confidence', '')}）")
                    catalyst_text = '\n'.join(parts)
                _enqueue("catalyst_analysis_start", {})
                _enqueue("catalyst_analysis_chunk", {"text": catalyst_text})
            _enqueue("catalyst_analysis_done", data)
```

- [ ] **Step 3: Update sync endpoint response**

In the sync `get_stock_business` function (the non-streaming endpoint), find where `data['track_quality']` is set. After it, add the catalyst analysis call following the same pattern (call `_build_catalyst_prompt` → `call_ai_structured` → `_parse_catalyst_analysis`). Use `call_type="catalyst_analysis"`.

- [ ] **Step 4: Verify Python syntax**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis && python3 -m compileall -q src api data_provider main.py server.py`
Expected: No output

- [ ] **Step 5: Commit**

```bash
git add api/v1/endpoints/stock_info.py
git commit -m "feat: add catalyst analysis SSE phase and cache path"
```

---

### Task 3: Frontend Types — Add CatalystAnalysis

**Files:**
- Modify: `apps/dsa-web/src/api/business.ts`

- [ ] **Step 1: Add CatalystAnalysis types**

Insert after the `TrackQualityAnalysis` interface (after line 113). Add:

```typescript
export interface CatalystItem {
  type: string;
  description: string;
  timeframe: string;
  confidence: '高' | '中' | '低';
  impact: '重大' | '中等' | '有限';
}

export interface CatalystAnalysis {
  overall_assessment: '催化充分' | '催化一般' | '催化不足';
  summary: string;
  catalysts: CatalystItem[];
  key_dates: string[];
  risks: string[];
  llm_used: boolean;
  model?: string;
  raw_text?: string;
  llm_input?: string;
  error?: string;
}
```

- [ ] **Step 2: Extend BusinessResponse**

Add `catalyst_analysis?: CatalystAnalysis` field to `BusinessResponse` interface, after `track_quality`.

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis/apps/dsa-web && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add apps/dsa-web/src/api/business.ts
git commit -m "feat: add CatalystAnalysis types"
```

---

### Task 4: Frontend Hook — Add Catalyst Streaming State

**Files:**
- Modify: `apps/dsa-web/src/hooks/useBusinessStream.ts`

- [ ] **Step 1: Add catalystStreamingText state**

Add a new state variable after `trackStreamingText`:

```typescript
const [catalystStreamingText, setCatalystStreamingText] = useState('');
```

- [ ] **Step 2: Reset in startStream**

In the `startStream` function, add reset alongside the other streaming text resets:

```typescript
setCatalystStreamingText('');
```

- [ ] **Step 3: Add SSE event listeners**

Add after the `track_analysis_chunk` listener. Add three listeners:

```typescript
es.addEventListener('catalyst_analysis_start', () => {
  // Catalyst analysis phase started
});

es.addEventListener('catalyst_analysis_chunk', (e) => {
  const data = JSON.parse(e.data);
  setCatalystStreamingText(prev => prev + data.text);
});

es.addEventListener('catalyst_analysis_done', (e) => {
  const data: BusinessResponse = JSON.parse(e.data);
  setBusiness(data);
  setPhase('done');
  es.close();
});
```

- [ ] **Step 4: Move terminal handling from track to catalyst**

The `track_analysis_done` listener (lines 111–116) currently sets phase to done and closes the EventSource. Change it to only update business state without closing:

Replace:
```typescript
es.addEventListener('track_analysis_done', (e) => {
  const data: BusinessResponse = JSON.parse(e.data);
  setBusiness(data);
  setPhase('done');
  es.close();
});
```

With:
```typescript
es.addEventListener('track_analysis_done', (e) => {
  const data: BusinessResponse = JSON.parse(e.data);
  setBusiness(data);
  // Catalyst phase follows; terminal handling moves to catalyst_analysis_done
});
```

- [ ] **Step 5: Update return value**

Update the return object to include `catalystStreamingText`:

```typescript
return {
  phase, progressEvents, streamingText, envStreamingText, trackStreamingText,
  catalystStreamingText,
  business, isCached, error, startStream, abort,
};
```

- [ ] **Step 6: Update the interface**

Add `catalystStreamingText: string` to `UseBusinessStreamResult`:

```typescript
export interface UseBusinessStreamResult {
  phase: BusinessStreamPhase;
  progressEvents: BusinessProgressEvent[];
  streamingText: string;
  envStreamingText: string;
  trackStreamingText: string;
  catalystStreamingText: string;
  business: BusinessResponse | null;
  isCached: boolean;
  error: string | null;
  startStream: (symbol: string, force?: boolean) => void;
  abort: () => void;
}
```

- [ ] **Step 7: Commit**

```bash
git add apps/dsa-web/src/hooks/useBusinessStream.ts
git commit -m "feat: add catalyst streaming state to useBusinessStream"
```

---

### Task 5: Frontend Component — Create CatalystCard

**Files:**
- Create: `apps/dsa-web/src/components/CatalystCard.tsx`

- [ ] **Step 1: Create the component**

```tsx
import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChevronRight, Zap } from 'lucide-react';
import { type CatalystAnalysis } from '../api/business';
import { cn } from '../utils/cn';

const CONFIDENCE_COLORS: Record<string, string> = {
  '高': 'bg-emerald-50 text-emerald-700',
  '中': 'bg-amber-50 text-amber-700',
  '低': 'bg-red-50 text-red-700',
};

const IMPACT_COLORS: Record<string, string> = {
  '重大': 'bg-emerald-50 text-emerald-700',
  '中等': 'bg-amber-50 text-amber-700',
  '有限': 'bg-slate-100 text-slate-600',
};

const ASSESSMENT_STYLES: Record<string, string> = {
  '催化充分': 'border-emerald-200 bg-emerald-50',
  '催化一般': 'border-amber-200 bg-amber-50',
  '催化不足': 'border-red-200 bg-red-50',
};

const ASSESSMENT_TEXT: Record<string, string> = {
  '催化充分': 'text-emerald-700',
  '催化一般': 'text-amber-700',
  '催化不足': 'text-red-700',
};

const TYPE_ICONS: Record<string, string> = {
  '业绩催化': '📊',
  '政策催化': '📜',
  '事件催化': '🎯',
  '行业催化': '🏭',
  '资金催化': '💰',
};

export default function CatalystCard({ analysis }: { analysis: CatalystAnalysis }) {
  const [inputExpanded, setInputExpanded] = useState(false);

  // Fallback: raw text rendering
  if (!analysis.overall_assessment && analysis.raw_text) {
    return (
      <div className="stock-analysis-panel">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
          <Zap className="h-4 w-4 text-amber-500" />催化分析
          {analysis.model && (
            <span className="ml-auto text-xs font-normal text-slate-400">
              {analysis.model.replace('openai/', '')}
            </span>
          )}
        </h3>
        <div className="prose prose-slate prose-sm max-w-none
          prose-p:text-sm prose-p:leading-relaxed prose-p:text-slate-600">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{analysis.raw_text}</ReactMarkdown>
        </div>
      </div>
    );
  }

  if (!analysis.overall_assessment) return null;

  const assessStyle = ASSESSMENT_STYLES[analysis.overall_assessment] || 'border-slate-200 bg-slate-50';
  const assessText = ASSESSMENT_TEXT[analysis.overall_assessment] || 'text-slate-700';

  return (
    <div className="stock-analysis-panel">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <Zap className="h-4 w-4 text-amber-500" />催化分析
        {analysis.model && (
          <span className="ml-auto text-xs font-normal text-slate-400">
            {analysis.model.replace('openai/', '')}
          </span>
        )}
      </h3>

      {/* Overall assessment banner */}
      <div className={cn('mb-4 rounded-lg border px-3 py-2', assessStyle)}>
        <div className="flex items-center gap-2">
          <span className={cn('text-sm font-semibold', assessText)}>
            {analysis.overall_assessment}
          </span>
          {analysis.summary && (
            <span className="text-sm text-slate-600">— {analysis.summary}</span>
          )}
        </div>
      </div>

      {/* Catalyst items */}
      {analysis.catalysts?.length > 0 && (
        <div className="space-y-2.5 mb-4">
          {analysis.catalysts.map((cat, i) => {
            const icon = TYPE_ICONS[cat.type] || '📌';
            const confColor = CONFIDENCE_COLORS[cat.confidence] || 'bg-slate-100 text-slate-600';
            const impColor = IMPACT_COLORS[cat.impact] || 'bg-slate-100 text-slate-600';
            return (
              <div key={i} className="border-b border-slate-50 pb-2.5 last:border-0 last:pb-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-sm">{icon}</span>
                  <span className="text-sm font-medium text-slate-700">{cat.type}</span>
                  <span className={cn('ml-auto rounded-full px-2 py-0.5 text-xs font-medium', confColor)}>
                    {cat.confidence}
                  </span>
                  <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', impColor)}>
                    {cat.impact}
                  </span>
                </div>
                <p className="text-sm leading-relaxed text-slate-600">{cat.description}</p>
                {cat.timeframe && (
                  <p className="mt-1 text-xs text-slate-400">⏰ {cat.timeframe}</p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Key dates */}
      {analysis.key_dates?.length > 0 && (
        <div className="mb-4 rounded-lg bg-slate-50 px-3 py-2">
          <p className="text-xs font-medium text-slate-400 mb-1.5">📅 关键时间窗口</p>
          <div className="flex flex-wrap gap-1.5">
            {analysis.key_dates.map((date, i) => (
              <span key={i} className="rounded bg-white px-2 py-0.5 text-xs text-slate-600 border border-slate-200">
                {date}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Risks */}
      {analysis.risks?.length > 0 && (
        <div className="mb-4">
          <p className="text-xs font-medium text-slate-400 mb-1.5">⚠️ 催化落空风险</p>
          <ul className="space-y-1">
            {analysis.risks.map((risk, i) => (
              <li key={i} className="text-xs text-slate-500 flex items-start gap-1.5">
                <span className="mt-0.5 text-red-400 shrink-0">•</span>
                {risk}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Collapsible LLM input */}
      {analysis.llm_input && (
        <div className="border-t border-slate-100 pt-3">
          <button
            type="button"
            onClick={() => setInputExpanded(!inputExpanded)}
            className="flex w-full items-center gap-1.5 text-left text-xs text-slate-400 transition-colors hover:text-slate-600"
          >
            <ChevronRight className={cn('h-3 w-3 transition-transform', inputExpanded && 'rotate-90')} />
            分析输入数据
          </button>
          {inputExpanded && (
            <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-slate-50 p-3 font-mono text-xs leading-relaxed text-slate-500 whitespace-pre-wrap">
              {analysis.llm_input}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/dsa-web/src/components/CatalystCard.tsx
git commit -m "feat: add CatalystCard component"
```

---

### Task 6: Frontend Page — Update Card Order + Streaming

**Files:**
- Modify: `apps/dsa-web/src/pages/StockAnalysisPage.tsx`

- [ ] **Step 1: Add import**

Add after the TrackQualityCard import (line 32):

```tsx
import CatalystCard from '../components/CatalystCard';
```

- [ ] **Step 2: Update card order in BusinessAnalysisPanel**

In the `BusinessAnalysisPanel` function, the card rendering order currently is:
1. TrackQualityCard
2. EnvironmentAnalysisCard
3. Business LLM analysis
4. Business Intro
5. Composition tables

Insert CatalystCard before TrackQualityCard:

```tsx
{business.catalyst_analysis?.llm_used && (
  <CatalystCard analysis={business.catalyst_analysis} />
)}
{business.track_quality?.llm_used && (
  <TrackQualityCard analysis={business.track_quality} />
)}
```

- [ ] **Step 3: Update BusinessAnalysisPanelStreaming**

In the streaming phase, destructure `catalystStreamingText` from the hook result. Update the streaming rendering to show 4 phases:

When `business` exists (analysis_done received), the rendering order should be:
1. Catalyst streaming card (if `catalystStreamingText` exists, show it; else show "AI 正在分析催化因素..." spinner)
2. Track streaming card (if `trackStreamingText` exists, show it)
3. Environment streaming card (if `envStreamingText` exists, show it)
4. Completed business LLM analysis
5. Remaining panels (with `llm_analysis: { llm_used: false }` to avoid duplicate)

The catalyst card in streaming should look like:

```tsx
const catalystCard = catalystStreamingText ? (
  <div className="stock-analysis-panel">
    <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
      <Zap className="h-4 w-4 text-amber-500" />
      <span className="animate-pulse">AI 正在分析催化因素</span>
    </h3>
    <div className="prose prose-slate prose-sm max-w-none
      prose-p:text-sm prose-p:leading-relaxed prose-p:text-slate-600">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{catalystStreamingText}</ReactMarkdown>
    </div>
  </div>
) : (
  <div className="stock-analysis-panel">
    <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
      <Zap className="h-4 w-4 text-amber-500" />
      <span className="animate-pulse">AI 正在分析催化因素...</span>
    </h3>
  </div>
);
```

And the rendering order inside the streaming phase:
```tsx
{catalystCard}
{trackCard}
{envCard}
{/* completed business analysis */}
<BusinessAnalysisPanel business={{ ...business, llm_analysis: { llm_used: false }, environment_analysis: undefined, track_quality: undefined, catalyst_analysis: undefined }} />
```

- [ ] **Step 4: Verify build**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis/apps/dsa-web && npm run build`
Expected: Build succeeds

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/pages/StockAnalysisPage.tsx
git commit -m "feat: integrate CatalystCard into business analysis page"
```

---

### Task 7: Tool Registry — Update Description

**Files:**
- Modify: `src/agent/tool_registry.py`

- [ ] **Step 1: Update get_stock_business description**

Find the `get_stock_business` tool description (around line 337). Add item #9 for catalyst analysis:

```python
"8. LLM 赛道质量评估（行业周期位置、未来 3 年空间、竞争强度，含同行财务对比）\n"
"9. LLM 催化分析（未来 6-12 个月催化剂判断：业绩/政策/事件/行业/资金催化，含关键时间窗口和落空风险）\n"
```

- [ ] **Step 2: Verify Python compiles**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis && python3 -m compileall -q src`
Expected: No output

- [ ] **Step 3: Commit**

```bash
git add src/agent/tool_registry.py
git commit -m "feat: update tool description with catalyst analysis"
```

---

### Task 8: Final Verification

- [ ] **Step 1: Full Python compile check**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis && python3 -m compileall -q src api data_provider main.py server.py`
Expected: No output

- [ ] **Step 2: Full frontend build**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis/apps/dsa-web && npm run build`
Expected: Build succeeds

- [ ] **Step 3: Manual SSE test**

Start the server and test the SSE stream for a stock (e.g., 600519). Verify the event sequence:
1. `connected`
2. `progress` × 5
3. `analysis_start` → `analysis_chunk` × N → `analysis_done`
4. `env_analysis_start` → `env_analysis_chunk` × N → `env_analysis_done`
5. `track_analysis_start` → `track_analysis_chunk` × N → `track_analysis_done`
6. `catalyst_analysis_start` → `catalyst_analysis_chunk` × N → `catalyst_analysis_done`

- [ ] **Step 4: Verify card order on page**

Open the business analysis tab. Completed card order should be:
1. 催化分析 (CatalystCard)
2. 赛道质量评估 (TrackQualityCard)
3. 外部环境分析 (EnvironmentAnalysisCard)
4. 业务动向分析 (LLM business analysis)
5. 业务概况 (Business Intro)
6. 主营构成 (Composition tables)

- [ ] **Step 5: Verify cache path**

Trigger a second request for the same stock on the same day. Verify that the cached response replays all 4 LLM phases correctly, including the catalyst analysis.
