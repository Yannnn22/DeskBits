# DeskBits Agent Service

Local-first Python Agent backend for DeskBits.

FastAPI + LangGraph service that powers chat, tools, skills, memory, knowledge base and model routing. It runs on the same machine as the desktop client and is started automatically by `npm run start:mac`.

## Stack

- Python 3.13
- FastAPI + Uvicorn
- LangGraph / LangChain
- SQLite + JSONL local storage
- Optional Redis and Milvus
- DeepSeek / OpenAI-compatible providers, plus an offline FakeProvider

## Setup

```bash
cd apps/agent-service
uv sync
```

Copy `.env.example` to `.env` only if you need local overrides. Never commit `.env`.

## Run

```bash
uv run python run.py
```

The service prints a single JSON line when ready:

```json
{"event":"desktopmate_ready","host":"127.0.0.1","port":12345,"auth_token":"..."}
```

Electron reads this line and injects the URL/token into the renderer. For manual runs, set a fixed port and token:

```bash
DESKTOPMATE_PORT=8000 \
DESKTOPMATE_AUTH_TOKEN=dev-token \
DESKTOPMATE_ALLOWED_ORIGINS=http://127.0.0.1:5173 \
uv run python run.py
```

## Health & runtime

```bash
curl -s http://127.0.0.1:8000/api/health
curl -s -H 'X-DesktopMate-Token: dev-token' http://127.0.0.1:8000/api/runtime
```

`/api/health` reports database, settings, model provider and speech components. `/api/runtime` reports the active provider, model source, vision and speech status.

## Test

```bash
env -u PYTHONPATH uv run pytest -q
env -u PYTHONPATH uv run pytest -m smoke
```

Lint:

```bash
uv run ruff check src tests
```

## Model provider

Default behavior without any key: `FakeModelProvider` returns deterministic local responses, so the full UI can be explored offline.

Resolution order:

```text
API key:  encrypted SettingsStore key > environment key > none (fake)
Model:    SettingsStore model > environment model > default
Provider: explicit provider with a valid key > env DeepSeek > env OpenAI > fake
```

Keys are encrypted with Fernet in the local settings directory; API responses only expose a mask.

## Data layout

Default root: `~/.desktopmate` (override with `DESKTOPMATE_APP_DATA_DIR`).

```text
~/.desktopmate/
  desktopmate.db       # sessions / messages / tasks / documents /
                       # chunks / memory / profile / settings
  projects.db          # project folder references
  knowledge/
    originals/         # uploaded files
    extracted/         # parsed text
  memory/              # memory_events.jsonl
  traces/              # run_*.jsonl
  drafts/              # draft artifacts
  settings/            # settings.json + encrypted keys.enc
```

## API groups

| Prefix | Purpose |
| --- | --- |
| `/api/chat`, `/ws/chat` | chat, streaming events, tool calls |
| `/api/sessions` | conversation history |
| `/api/tasks`, `/api/reminders` | task list and reminders |
| `/api/knowledge` | upload, indexing, chunks, retrieval, evaluation |
| `/api/skills` | skill packages and tool metadata |
| `/api/fs`, `/api/files` | allowed-root file tree, read, preview |
| `/api/attachments` | uploads and attachment metadata |
| `/api/profile`, `/api/settings` | profile, models, keys, runtime settings |
| `/api/tools` | tool registry and risk levels |

All endpoints except health check require `X-DesktopMate-Token`.

## Security

- Random local token for HTTP / WebSocket.
- Fernet-encrypted API keys.
- `DESKTOPMATE_ALLOWED_ROOTS` constrains file tools.
- Write tools (`draft_write`, `file_patch`, knowledge save) require user approval.
- Memory writes reject sensitive keys such as `api_key`, `token`, `password`.

## Docs

- [Architecture](../../docs/architecture.md)
- [Release notes](../../docs/release-notes.md)
- [Root README](../../README.md)
- [Desktop Shell](../desktop-shell/README.md)
