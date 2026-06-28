---
name: BCS-003 拆解 analyzer 巨型模块
description: 把 3400+ 行的 analyzer 按 Prompt 构造、响应解析、结构性风险、报告完整性等职责拆开
type: human-plan
---

# BCS-003 拆解 `analyzer` 巨型模块

- Plan ID: BCS-003
- Version: 3
- Owner Skill: `dev`
- Status: `complete`
- Frontend Impact: `no`
- Requirement Baseline: `src/analyzer.py` 单文件 3407 行，里面包含：LiteLLM 调用 + 路由、响应 JSON 修复与字段填充、报告完整性检查、结构性风险判定、决策稳态化、筹码 / 趋势 / 价格位置等子模块逻辑、`AnalysisResult` 数据类。新增字段或调整 Prompt 需要在数千行中翻动，且不同子模块逻辑相互拼贴，可读性极差。
- Confirmed Decisions: 无
- Current Plan:
  - 为什么修：AI 报告生成链路是项目最核心也是最常变动的部分，单文件过大导致 Prompt 调整、字段对齐、字段稳定性逻辑互相干扰，回归风险高。
  - 修完行为会怎样：
    - 保留对外导出符号：`GeminiAnalyzer`、`get_analyzer`、`AnalysisResult`，以及现有测试或调用方直接 import 的辅助函数；旧调用方零修改即可继续工作。
    - 按职责拆为彼此独立的模块组：Prompt 与上下文构造、LLM 调用与重试、响应解析与 JSON 修复、报告完整性 / 占位符填充、结构性风险与决策稳态化、子结构（筹码 / 趋势 / 价格位置）、`AnalysisResult` 数据类。每个模块只负责单一职责，可在不打开其他模块的情况下独立阅读和修改。
    - 对外行为完全不变：报告内容、字段语义、字段稳定化逻辑触发条件、LLM 调用顺序与重试策略、错误处理与日志、报告语言本地化策略均保持现状。
  - 明确不做什么：
    - 不修改任何 Prompt 文本与字段语义；
    - 不调整 `AnalysisResult` 字段集合或默认值；
    - 不改变 LLM 调用顺序、模型路由、重试策略；
    - 不修改报告本地化（中 / 英）逻辑；
    - 不顺手新增 / 删除任何业务规则、不调整结构性风险阈值；
    - 不重写测试，不调整测试断言。
  - 怎么验收：
    - `analyzer` 相关现有测试集合（覆盖 Prompt 拼装、筹码结构回填、买点判定、决策稳态化、报告完整性等场景）零修改即可全部通过；
    - 项目可正常 import 和编译，无语法或导入错误；
    - 旧调用方以原有 import 路径继续可用，无符号缺失；
    - 拆分后实际逻辑分布在按职责命名的子模块中，原 `analyzer` 入口仅作为兼容再导出层，任一新模块体量与可读性显著优于原单文件。
- Changes Since Last Plan: 验收条目去除测试命令与具体测试文件路径，改写为需求级覆盖面与可用性描述。
- Unchanged Scope: Prompt 内容、字段语义、LLM 调用与重试策略、本地化逻辑、结构性风险与决策稳态化阈值、现有测试集合
- Needs Reconfirmation: 无
- Review Status: v3 plan-check 通过；design-check 不适用（Frontend Impact=no）；v3 audit 通过，流程结束
- Delivery Status: v3 approved 并已实施；`src/analyzer.py` 拆分为 `src/analyzer/` 包（共 11 个模块，原 3407 行 → 单模块上限 2026 行，其余 42–531 行）；旧 import 路径与所有 `_`-前缀私有符号继续可用；`python3 -m compileall` 通过；85 个 analyzer 相关测试零修改通过（`test_analysis_history.py` 的失败已确认在 pristine main 上同样存在，与本次重构无关）。
- Revision Notes: 无
