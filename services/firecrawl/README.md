# Firecrawl

Firecrawl is project infrastructure. Its official source is cloned into
`services/firecrawl/app` at a pinned revision, using the same project-local
dependency pattern as RSSHub. `./dev.sh start` builds and starts the local,
keyless service together with RSSHub, the backend, and the frontend; no address
or API key needs to be configured in the application settings.

The API is only exposed on `http://127.0.0.1:3002`. The install script builds the
API and Playwright service directly from the pinned Firecrawl source. A local
Redis process is started on port 6380 for synchronous search/scrape state. No
Docker, container image, Colima, PostgreSQL, or RabbitMQ service is used.

For `/v2/search`, Firecrawl uses the project-managed SearXNG service on port
8888. SearXNG aggregates the configured keyless search engines and receives the
user's original query without keyword rewriting. Exa and Parallel are handled
outside Firecrawl as remote fallbacks only.

Useful commands:

First-time native installation requires Git, Node.js 22, pnpm, Redis and a Rust
toolchain; the WebUI/RSSHub use Node.js 24 separately. With nvm, install both
versions before starting. The installer may install Redis through Homebrew and
Rust through rustup if missing, and downloads/builds upstream dependencies and
browser binaries. Prepare these prerequisites before using `dev.sh` on a new
machine. Inspect `scripts/install-firecrawl.sh` for the pinned revision and
exact build steps.

The downloaded upstream source is AGPL-3.0; see its `LICENSE` and the repository's
[third-party notice](../../THIRD_PARTY_NOTICES.md).

```bash
./dev.sh start
./dev.sh status
./dev.sh stop
```
