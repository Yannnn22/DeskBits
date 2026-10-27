# DeskBits Desktop Shell

Electron + Vue 3 renderer for the DeskBits macOS desktop client.

## Stack

- Electron 43
- Vue 3.5 + TypeScript + Vite 5
- macOS native window, floating pet window, preload IPC
- WebSocket / SSE client for the local Agent Service

## Structure

```text
apps/desktop-shell/
├── electron/
│   ├── main.cjs             # windows, pet, app lifecycle
│   ├── preload.cjs          # contextBridge IPC surface
│   └── agent-service.cjs    # Python child-process manager
├── src/
│   ├── App.vue              # conversation / workspace / settings / pet UI
│   ├── api.ts               # Agent Service client
│   ├── styles.css
│   └── assets/              # brand, pet sprites
├── scripts/start-mac.cjs    # LaunchServices launcher
├── package.json
└── vite.config.ts
```

## Setup

```bash
corepack pnpm install
```

## Development

Browser-only Vite development:

```bash
npm run dev
# http://127.0.0.1:5173
```

Electron development (two terminals):

```bash
npm run dev            # Vite
npm run dev:electron   # Electron
```

One-command macOS launch (recommended):

```bash
npm run build      # start:mac loads dist/index.html; build once after clone
npm run start:mac
```

`start:mac` uses LaunchServices to launch Electron and auto-starts the local Python Agent Service. For live development, use `npm run dev` + `npm run dev:electron`. The backend detection order is:

1. `DESKTOPMATE_AGENT_SERVICE_DIR`
2. `apps/agent-service`（优先使用其 `.venv/bin/python`）
3. `uv run python`

## External service mode

If you want to manage the Agent Service yourself:

```bash
DESKTOPMATE_AGENT_SERVICE_URL=http://127.0.0.1:8000 \
DESKTOPMATE_AGENT_AUTH_TOKEN=your-token \
npm run start:mac
```

For browser-only Vite development, provide the same values as `VITE_AGENT_SERVICE_URL` and `VITE_AGENT_AUTH_TOKEN`.

## Build

```bash
npm run build
npm run start
```

`npm run build` runs `vue-tsc --noEmit` followed by `vite build`.

## Current UI scope

- Conversation workspace with streaming replies, composer modes, attachments, voice controls and local drafts.
- Workspace library for documents, checklists, tasks, knowledge items and file artifacts.
- Project sidebar with folder references, file tree, project conversations and README context.
- Personal knowledge base cards with indexing status, citations and "add to conversation".
- Settings for profile, pet, runtime, reminders, models, skills, API keys and privacy.
- Floating pet window with compact / conversation modes and speech hooks.

## Links

- [Root README](../../README.md)
- [Agent Service](../agent-service/README.md)
- [Architecture](../../docs/architecture.md)
