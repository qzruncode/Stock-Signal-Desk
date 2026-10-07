# RSSHub

This directory controls a project-local RSSHub instance. RSSHub source is cloned into `services/rsshub/app`, which is ignored by git.

The project dev script starts this service together with the backend and frontend:

```bash
./dev.sh start
```

Manual start:

```bash
cd services/rsshub
npm start
```

The API backend defaults to `http://127.0.0.1:1200`.

First install with Node.js 24 selected:

```bash
cd services/rsshub
npm run install:rsshub
```

The installer fetches a pinned upstream revision, installs its locked pnpm
dependencies and downloads Chromium for browser-backed routes. Browser download
failure leaves those routes unavailable; rerun installation after fixing the
network. The upstream source is AGPL-3.0; see its `LICENSE` and the repository's
[third-party notice](../../THIRD_PARTY_NOTICES.md).
