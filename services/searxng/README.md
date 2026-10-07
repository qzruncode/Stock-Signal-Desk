# SearXNG

SearXNG is the project's local web-search backend. The official source is
cloned into `services/searxng/app` at a pinned revision and installed in the
project-local `services/searxng/.venv`. It starts with `./dev.sh start`; no
Docker, Colima, hosted account, or API key is required.

The service listens only on `http://127.0.0.1:8888`. Firecrawl sends the user's
original query to SearXNG and aggregates the configured keyless search engines.
Exa and Parallel remain application-level fallbacks if this local search chain
cannot return results.

First-time installation requires Git and Python with virtual-environment support
and downloads dependencies into this service's own environment. See
`scripts/install-searxng.sh` for the pinned revision. Upstream source is
AGPL-3.0; see its `LICENSE` and the repository's
[third-party notice](../../THIRD_PARTY_NOTICES.md).
