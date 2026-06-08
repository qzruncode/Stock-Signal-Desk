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
