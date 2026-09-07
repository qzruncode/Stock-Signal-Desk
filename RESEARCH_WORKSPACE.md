# 研究工作区维护说明

## 边界与复用

Agent 仍只有原生 `create_agent` 的模型/工具循环。审批、证据校验、原生 checkpoint、取消与恢复的权威没有迁移到前端缓存。

| 能力 | 复用入口 | 本次补充 |
| --- | --- | --- |
| 回归实验 | LangGraph 真图、GenericFakeChatModel、ToolRegistry、AgentEvals、既有质量评分 | 版本化夹具、冻结工具、报告与基线比较 |
| 用量 | AIMessage.usage_metadata、UsageMetadataCallbackHandler、现有 LLMUsage | 幂等持久化、实报覆盖率；与估算预算分开 |
| 长对话 | LangChain SummarizationMiddleware | 运行级模型绑定、失败不替换历史、摘要不进入用户流 |
| 研究档案 | StructuredAgentAnswer、金融结论/结果表、现有证据校验 | 已核验回答的显式研究注解、证据冻结与归档页面 |
| 变化提醒 | 自选分组、已有新闻/财务/日线、提醒表、企业微信通知服务、资源租约 | 默认关闭、首次基线、变化差异、冷却与持久化发送认领 |
| 工作区 | 既有页面组件、assistant-ui、TanStack Query、React Hook Form | 业务导航、HTTP 查询缓存、聊天控制与会话操作分层 |

## 使用

- `/research` 查看研究档案；可按股票代码筛选、更新复盘、跳转原始运行。
- 只有新完成、通过证据检查且包含显式 `research` 注解的判断自动归档。不会从历史 Markdown 猜测买卖结论；旧金融结论仍可读取。
- `block_indices` 为回答区块的一基索引，只归档明确判断及其相关风险。股票范围必须与证据一致。
- 保留原始结论和证据，不覆盖旧版本。有关联研究的运行不受常规 7 天运行清理影响；用户显式删除会话仍按原有级联删除语义处理。
- 复盘使用已同步日线，基准取结论日期之前的有效收盘价；仅使用今天之前的已存储有效日线，观察 5/20/60 条交易日记录。缺行情保持待积累，不补造交易价格。参考收益包含基准到结论时刻间的价格变化，不是成交收益、回测或可交易策略准确率；行情缺失、复权口径变化也会影响可比性。
- 新计算标记为 `financial-outcome-1.1`，旧版已完成结果和原有基准不重写；比较历史结果时需同时检查计算版本与基准日期。
- 提醒可创建、编辑、启停、立即检查，目标为股票或已有自选分组（最多 200 只）。可选择新闻、财务、研究变化及日线收盘价跌破条件。改变目标或参数会重建基线。
- 检查与外部推送两个开关分别授权，默认均关闭。首次观察及新加入的分组成员只建立基线；冷却期内保留未报告变化。新闻仅比较近 30 天最近 30 条已同步条目，新条目才触发；删除或过期不触发。
- 每分钟维护循环查到期规则并更新复盘，复用数据库资源租约。快照使用 SQLAlchemy 窗口查询，一个分组只需四次数据查询，不按每只股票重复请求。单条规则失败不阻断其他规则，错误显示在规则中并退避五分钟。
- 通知先持久化认领再发送；未确认的发送不自动重发，避免重复通知。`sending` 也可能表示进程在发送期间退出，需要人工核对，不承诺远端 exactly-once。未启用推送只写站内记录；数据尚未同步时不会主动联网补数，更不会下单。

## 用量与摘要

`/monitoring` 展示实报 Token 和报告覆盖率。运行 API 的 `actual_usage`（数据库 `agent_runs.usage_json`）/监控的 `workload_24h.actual_usage` 来源为供应商元数据；无报告是未知，不是零。回调 ID 去重，重复回放不累加；累计流 usage 只采用最后一份。缓存输入/推理 Token 细节保留在用量中。没有新增按猜测单价换算的实际费用；原预算金额仍是估算。

`AGENT_SUMMARY_TRIGGER_TOKENS` 默认 60000，0 关闭。原生摘要保留最近 20 条消息及工具配对，证据与 claim 账本是独立 checkpoint channel，不被摘要覆盖。摘要通过同一受预算/租约保护的模型，计入当前运行用量；供应商异常时保留原始历史。

## 可复现环境

运行锁文件针对 Python 3.13：`requirements.lock` 为 Linux x86_64，`requirements-macos.lock` 为 Intel macOS。不要把这些平台锁当成 Windows、ARM 或 Python 3.14 的兼容性承诺；新平台使用相同声明单独解析并验收。

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements-macos.lock -r requirements-eval.lock
python -m pip check

source ~/.nvm/nvm.sh
nvm use 24
cd apps/dsa-web
npm ci
```

Linux 使用 `requirements.lock` 替换上例的 macOS 锁。`requirements-eval.lock` 只用于开发/CI；前端保留既有 postinstall，不新增第三方补丁。不得升级正在运行的全局 Python 来满足测试。

使用 uv 0.12.9 更新锁（默认复用现有锁版本；确需升级时明确指定 `--upgrade-package`）：

```bash
uv pip compile requirements.txt --python-version 3.13 --python-platform x86_64-unknown-linux-gnu --generate-hashes --index-url https://pypi.org/simple --no-annotate -o requirements.lock
uv pip compile requirements.txt -c requirements.lock --python-version 3.13 --python-platform x86_64-apple-darwin --generate-hashes --index-url https://pypi.org/simple --no-annotate -o requirements-macos.lock
uv pip compile requirements-eval.txt -c requirements.lock --python-version 3.13 --python-platform x86_64-unknown-linux-gnu --generate-hashes --index-url https://pypi.org/simple --no-annotate -o requirements-eval.lock
```

## 数据库升级

Schema `2026.09.06.1` 为增量迁移：实报 usage、提醒所有权/状态/到期时间及触发去重索引。旧字段和数据不清空。生产发布前停止写入并使用现有 `scripts/manage_database.py backup --output <备份路径>` 备份，再执行 `migrate` 和 `check`。checkpoint 数据库也需按现有运维流程备份。SQLite 开发模式启动可执行幂等迁移；生产的 schema 检查仍会拒绝未迁移库。

本次测试只创建临时数据库；测试和评测不得指向真实业务库。回滚应使用对应代码版本及匹配的备份，不自动执行逆向删列。

## 离线实验与定向门禁

```bash
python scripts/evaluate_agent.py --output reports/agent-regression.json
python scripts/evaluate_agent.py --baseline reports/agent-regression.json --output reports/agent-comparison.json
```

默认冻结模型和工具，只检查真实执行/证据/答案契约链路，不是模型能力成绩。使用官方 AgentEvals 比较工具轨迹，沿用项目质量评分，记录 dataset/prompt SHA、Git SHA、dirty 状态与逐例耗时。不同数据集禁止比较。数据与 trace 默认留在本地，即使 shell 开启 LangSmith tracing 也不上传。

只有显式传入 `--live-model-config <本地模型配置.json>` 才调用模型（会产生费用），工具仍被冻结；可用 `--prompt-file` 对照 Prompt。配置文件不写入报告，勿提交密钥。先在同一数据集上建立基线再比较。不把冻结用例的通过率等同真实财经数据或模型质量。

GitHub 工作流 `research-quality.yml` 仅运行固定的相关后端/前端测试，不部署、不发送真实通知、无模型密钥；工作流写入不代表远端 CI 已运行。前端另提供基于 TypeScript 官方编译 API 的定向检查和已有约束脚本的 `--files` 模式：

```bash
cd apps/dsa-web
node scripts/typecheck-files.mjs src/pages/ResearchPage.tsx src/components/research/ResearchAlerts.tsx src/hooks/useChatController.ts
node scripts/check-code-constraints.mjs --files src/pages/ResearchPage.tsx src/hooks/useChatController.ts
npx vitest run src/pages/ResearchPage.test.tsx
```

类型检查解析导入以获取真实类型，但只报告指定根文件的诊断，不等同全项目 `tsc -b`。检查组件测试时将现有 `src/setupTests.ts` 一并作为根文件，以加载断言类型。本次不运行仓库全量 lint/typecheck/build，也不据此宣称生产端到端验收通过。

## 上游依据

- [LangChain 短期记忆和摘要](https://docs.langchain.com/oss/python/langchain/short-term-memory)
- [LangChain Token 用量](https://docs.langchain.com/oss/python/langchain/models#token-usage)
- [AgentEvals 官方轨迹评估](https://github.com/langchain-ai/agentevals/blob/main/python/README.md)
- [TanStack Query 缓存与取消](https://tanstack.com/query/latest/docs/framework/react/guides/query-cancellation)
- [uv 锁文件编译](https://docs.astral.sh/uv/pip/compile/)
- [GitHub 官方 Python setup](https://github.com/actions/setup-python)
