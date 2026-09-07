# Market Data Service

Independent acquisition, database, durable scheduling and versioned data API.
The business application's database and Agent runtime are not dependencies.

The serving API never silently claims old data is latest. Dataset coverage and
per-security freshness are separate; provider outage, unknown source time,
pending refresh and partial coverage are explicit states.

Runtime: FastAPI + SQLAlchemy + PostgreSQL + Celery/Redis. SQLite is supported for
isolated local tests. Celery beat dispatches persisted work; workers acquire
fenced leases and persist per-symbol outcomes. Business processes only use HTTP.

See [部署、迁移与运维说明](OPERATIONS.md) for setup, cutover, API, freshness
contracts, recovery and verification. The service can be deployed separately;
its image contains neither the business code nor LangChain/LangGraph.

See [本次验收记录](VALIDATION.md) for focused tests, real HTTP/browser E2E,
native runtime evidence and explicit verification limits.
