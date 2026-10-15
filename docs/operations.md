# Operations

Running the Furniture Assistant web app in production. For configuration keys see [Configuration](configuration.md); known limitations are tracked in [Production readiness](production-readiness.md).

## Running the app

```bash
make web-build          # once: build the frontend into webapp/dist
make web WEB_PORT=8010  # serve API + built frontend (default port 8000)
```

`make web` runs `uv run uvicorn src.webapp.api:app --host 127.0.0.1 --port $(WEB_PORT)`. For production, drop `--reload` and run under a supervisor (example below). The backend serves the built frontend from `webapp/dist`; `make web-ui` (Vite on :5173) is for frontend development only.

## Single process only

The app must run as **exactly one uvicorn worker and one replica**:

- `ProjectStore` serialises read-modify-write per project with in-process `threading.RLock` instances (`src/webapp/store.py`) — a second process would have its own lock set and races would corrupt module files.
- Analysis and refine jobs are `asyncio.Task` objects in `app.state.tasks` (`src/webapp/api.py`) — a second process would not see them, and both would accept the same job.

Do not pass `--workers N`, and do not scale to multiple replicas behind a load balancer. All durable state is plain files (see [Storage and backups](#storage-and-backups)), so this constraint is about locks and job tracking, not data location.

## Model server

Two supported options; both are selected purely by configuration:

| Option | Settings |
|---|---|
| LM Studio (local) | Start LM Studio, load the model, enable its server on :1234 (`make serve` opens the app). Defaults need no changes: `FA_API_BASE=http://127.0.0.1:1234/v1`, `FA_ORCHESTRATOR_MODEL=qwen/qwen3.6-35b-a3b`. |
| OpenAI | `FA_API_BASE=https://api.openai.com/v1`, `OPENAI_API_KEY` in the environment (or `.env`), `FA_ORCHESTRATOR_MODEL` set to an OpenAI model name. A base URL containing `api.openai.com` switches `src/furniture/interpret.py` to OpenAI's Responses API (OpenAI reasoning models reject the function tools used for structured output on Chat Completions). |

The web app only talks to the orchestrator model; `FA_VISION_MODEL` / `FA_VISION_API_BASE` affect the legacy pipeline (`make run`, `make run-project`). `GET /api/health` reports the configured `model_base_url` so a probe can verify which server the app was started against.

## Authentication

Auth is optional and off by default: it is enabled iff `FA_USERS_FILE` (default `config/users.yaml`) exists and lists at least one user. Manage users with the CLI (`src/webapp/users.py`):

```bash
uv run python -m src.webapp.users add alice      # prompts for the password (--password also works)
uv run python -m src.webapp.users list
uv run python -m src.webapp.users remove alice
```

Sessions: the `fa_session` cookie (HMAC-signed `username|expiry`), `httponly`, `samesite=lax`, lifetime 7 days. The `secure` flag defaults on in production (`FA_ENV=production`) or when `FA_PUBLIC_URL` starts with `https://`. `FA_COOKIE_SECURE` explicitly overrides this default. With auth disabled every request is attributed to `system` in the audit log.

## Session secret

Session cookies are signed with:

1. `FA_SESSION_SECRET` if set. It is **required in production**; the app fails at startup without it. Supply it through a protected environment source such as a systemd credential or secret manager, not a `.env` file.
2. Otherwise, in development only, an auto-generated `config/.session_secret` (32 random bytes, mode 0600, gitignored). This fallback lives inside the working tree and rotating it invalidates all sessions.

Production does not load `.env` and never creates the fallback file. Secret manager integration remains a deployment follow-up; provide the secret to the process environment until then.

## Health endpoint

`GET /api/health` is public even when auth is enabled (exempt in `src/webapp/auth.py`):

```json
{"status": "ok", "model_base_url": "http://127.0.0.1:1234/v1", "projects_root_writable": true, "auth_enabled": false}
```

`status` is `degraded` when the projects root cannot be written. Use it for load-balancer / systemd / container probes. Full endpoint reference: [API](api.md).

## Upload limits

Enforced at project creation (`HTTP 413`):

| Env | Default | Check |
|---|---|---|
| `FA_MAX_UPLOAD_MB` | `50` | per-file size |
| `FA_MAX_FILES` | `50` | files per project |

File types are restricted to drawing formats (PDF/PNG/JPG/TIFF/DWG/P3D); anything else is rejected with `400`.

## Jobs across restarts

Jobs live in memory only. On restart, a job left `queued`/`running` is reported as `error` ("Analysis interrupted (server restarted)") the next time the project is read, with an audit entry; the user re-runs the analysis from the UI. Already-built modules, history and overrides survive — only the in-flight run is lost.

## Storage and backups

All state is plain files under the projects root — `<FA_OUTPUT_DIR>/webapp/projects`, default `results/webapp/projects`. Each project directory contains `project.json`, `uploads/`, `pages/` (cached PNG renders), `modules/` with `history/` (versions, `history-archive-*` after a re-analysis), `audit.jsonl`, `job.json`, `pricing.yaml`, `quote.xlsx`/`quote.json` and `exports/`.

- **Back up the projects root directory** — that is the whole application state. Configuration (`config/`), `users.yaml` and the session secret are managed separately.
- `pages/` is a regenerable cache (hundreds of MB for multi-page packages): it can be excluded from backups and pruned; pages re-render on next view.
- `history-archive-*` directories grow with every re-analysis; prune old ones if you do not need rollback that far.

## Logs

The app logs to stdout/stderr only (standard `logging` + uvicorn access logs); there are no log files and no structured logging yet (see [Production readiness](production-readiness.md)). Under systemd, journald captures everything: `journalctl -u furniture-assistant -f`. Legacy pipeline runs can additionally be traced to Langfuse (`FA_TRACING_ENABLED`, `docker-compose.dev.yml`).

## Production checklist

- Set `FA_ENV=production`; this disables `.env` loading.
- Supply `FA_SESSION_SECRET` through a protected process environment / credential source. Startup fails if it is missing.
- Set `FA_USERS_FILE` to a protected users file with at least one account.
- Set `FA_PUBLIC_URL=https://…` when the public endpoint uses HTTPS. This defaults the cookie to `Secure`; `FA_COOKIE_SECURE` can explicitly override it.
- Keep the app behind TLS termination, bind uvicorn to loopback, and run one worker / one replica.
- Keep model/API keys out of the repository and `.env`; inject them through the service environment or credential mechanism.

## systemd unit example

```ini
[Unit]
Description=Furniture Assistant web app
After=network.target

[Service]
WorkingDirectory=/opt/furniture-assistant
User=furniture
Environment=FA_ENV=production
Environment=FA_API_BASE=http://127.0.0.1:1234/v1
Environment=FA_ORCHESTRATOR_MODEL=qwen/qwen3.6-35b-a3b
Environment=FA_OUTPUT_DIR=/var/lib/furniture-assistant
Environment=FA_USERS_FILE=/etc/furniture-assistant/users.yaml
Environment=FA_SESSION_SECRET=change-me-openssl-rand-hex-32
Environment=FA_PUBLIC_URL=https://furniture.example.com
ExecStart=/opt/furniture-assistant/.venv/bin/uvicorn src.webapp.api:app --host 127.0.0.1 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Replace the placeholder session secret with a protected credential source before deployment. One worker (no `--workers` flag), one replica. `OPENAI_API_KEY` must also be injected through the service environment or credential mechanism; production never reads it from `.env`.
