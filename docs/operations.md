# Operations

Running the Furniture Assistant web app in production. For configuration keys see [Configuration](configuration.md); known limitations are tracked in [Production readiness](production-readiness.md).

## Running the app

```bash
make web-build          # once: build the frontend into webapp/dist
make web WEB_PORT=8010  # serve API + built frontend (default port 8000)
```

`make web` runs `uv run uvicorn src.webapp.api:app --host 127.0.0.1 --port $(WEB_PORT)`. For production, drop `--reload` and run under a supervisor (example below). The backend serves the built frontend from `webapp/dist`; `make web-ui` (Vite on :5173) is for frontend development only.

## Process and host constraints

Multiple uvicorn workers on **one host** may share a projects root: project read-modify-write operations use a reentrant thread lock plus an exclusive `fcntl.flock` on each project's `.lock` file. Async job paths acquire and hold that lock in worker threads, not on the event loop. Job intent, owner (`host:pid`), per-run claim token and heartbeat are persisted in `job.json`; stale runners are fenced from later job/module writes.

Multiple hosts are supported only when they use the same shared projects filesystem and that filesystem provides reliable cross-host `flock` semantics plus atomic rename. Separate local disks and filesystems that do not honor `flock` are not supported. Workspace pricing updates have their own lock. User-file changes are performed by the CLI, not by API workers.

Project creation uses exclusive directory creation to avoid replacing an existing project if generated identifiers collide.

## Model server

Two supported options; both are selected purely by configuration:

| Option | Settings |
|---|---|
| LM Studio (local) | Start LM Studio, load the model, enable its server on :1234 (`make serve` opens the app). Defaults need no changes: `FA_API_BASE=http://127.0.0.1:1234/v1`, `FA_ORCHESTRATOR_MODEL=qwen/qwen3.6-35b-a3b`. |
| OpenAI | `FA_API_BASE=https://api.openai.com/v1`, `OPENAI_API_KEY` in the environment (or `.env`), `FA_ORCHESTRATOR_MODEL` set to an OpenAI model name. A base URL containing `api.openai.com` switches `src/furniture/interpret.py` to OpenAI's Responses API (OpenAI reasoning models reject the function tools used for structured output on Chat Completions). |

The web app only talks to the orchestrator model; `FA_VISION_MODEL` / `FA_VISION_API_BASE` affect the legacy pipeline (`make run`, `make run-project`). `GET /api/health` reports the configured `model_base_url` so a probe can verify which server the app was started against.

## Authentication and roles

Auth is optional and off by default: it is enabled iff `FA_USERS_FILE` (default `config/users.yaml`) exists and lists at least one user. Manage users with the CLI (`src/webapp/users.py`):

```bash
uv run python -m src.webapp.users add alice --role admin   # prompts for the password (--password also works)
uv run python -m src.webapp.users set-role alice editor
uv run python -m src.webapp.users list                     # username and role
uv run python -m src.webapp.users remove alice
```

Sessions: the `fa_session` cookie (HMAC-signed `username|expiry`), `httponly`, `samesite=lax`, lifetime 7 days. The `secure` flag defaults on in production (`FA_ENV=production`) or when `FA_PUBLIC_URL` starts with `https://`. `FA_COOKIE_SECURE` explicitly overrides this default; it accepts exactly `true`/`false`, `yes`/`no`, or `1`/`0` without surrounding whitespace, case-insensitively. Any other value fails startup.

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

`status` is `degraded` when the projects root cannot be written. This endpoint reports the configured model URL but does not actively contact the model server. Before accepting `analyze` or `refine`, the API separately calls `GET {FA_API_BASE}/models` with a 5-second timeout; connection failures, timeouts and non-2xx responses return `503` without queuing work or changing the module. Use `/api/health` for application/storage health and the analysis/refine response to verify model availability. Full endpoint reference: [API](api.md).

## Upload limits

Enforced on project creation and quote import (`HTTP 413`). The total request body is
rejected before multipart parsing when its `Content-Length` exceeds the cap; streamed
requests without that header are bounded as chunks arrive. Per-file size is checked from
the multipart parser's file metadata before file contents are read into application memory.

| Env | Default | Check |
|---|---|---|
| `FA_MAX_REQUEST_MB` | `200` | total multipart request body, including form boundaries |
| `FA_MAX_UPLOAD_MB` | `50` | per-file size |
| `FA_MAX_FILES` | `50` | files per project |

File types for project uploads are restricted to drawing formats (PDF/PNG/JPG/TIFF/DWG/P3D); anything else is rejected with `400`.

## Jobs across restarts

`job.json` stores job kind, owner (`host:pid`), per-run claim token, UTC heartbeat, status and per-page progress. A worker refreshes the heartbeat every five seconds; startup and job reads claim an analysis whose owner is dead or whose heartbeat is older than 30 seconds under the project lock. It audits `analyze_resume`, archives old module history, clears current modules and reruns the analysis from the beginning. This deliberately favors a clean, duplicate-free result over replaying a partially completed page; existing page records are not used to skip work.

All hosts sharing the project filesystem must keep their clocks synchronized; keep clock skew well below the 30-second stale threshold (preferably under five seconds). UTC-aware heartbeats are stored for new jobs; legacy naive timestamps are interpreted in the server's local timezone, then converted to UTC.

An interrupted refine is **not** retried automatically: startup or a job read marks its job and module refine status as `error`, adds a `refine_interrupted` audit entry, and the user can explicitly request the refine again. This avoids silently repeating a model call after its outcome may have been partially applied. Fresh jobs owned by another worker remain `running`, and concurrent analyze/refine requests receive HTTP 409.

## Project deletion and trash

`DELETE /api/projects/{id}` moves the entire project directory out of the projects
root to `<FA_OUTPUT_DIR>/webapp/trash/<id>-<timestamp>` (default:
`results/webapp/trash/`) and writes a `delete_project` audit entry into the moved copy.
Nothing is recursively removed by the API. The trashed directory retains its
project files and audit log but is no longer listed or accessible through project
endpoints. Deletion returns `409` while analysis or a model re-run is active.

To restore a project, move its directory back under
`<FA_OUTPUT_DIR>/webapp/projects/` using the original project ID (the portion before
the timestamp suffix); do not overwrite an existing project directory. To purge a
deleted project permanently, remove its selected directory from `trash/` only after
confirming it is no longer needed and required backups are complete.

## Storage and backups

All state is plain files under `<FA_OUTPUT_DIR>/webapp/` (default `results/webapp/`).
This includes the active `projects/` root and `trash/`, which contains deleted projects
that may still be needed for recovery. Each project directory contains `project.json`,
`uploads/`, `pages/` (cached PNG renders), `modules/` with `history/` (versions,
`history-archive-*` after a re-analysis), `audit.jsonl`, `job.json` (latest job and its
model usage), `usage.json` (cumulative model usage), `pricing.yaml`,
`quote.xlsx`/`quote.json` and `exports/`.

- **Back up `<FA_OUTPUT_DIR>/webapp/`** — back up both `projects/` and `trash/` together so trashed projects are not lost during a restore. Configuration (`config/`), `users.yaml` and the session secret are managed separately.
- `pages/` is a regenerable cache (hundreds of MB for multi-page packages): it can be excluded from backups and pruned; pages re-render on next view.
- `history-archive-*` directories grow with every re-analysis; prune old ones if you do not need rollback that far.

`uv run python -m src.webapp.maintenance` provides consistent backups and explicit retention cleanup. Both commands resolve `<FA_OUTPUT_DIR>/webapp/projects` using the same `config/default.yaml` and `FA_OUTPUT_DIR` settings as the web app.

```bash
# Create a gzip-compressed backup; regenerable pages/ PNGs are omitted by default.
uv run python -m src.webapp.maintenance backup --output /var/backups/furniture-projects-$(date +%F).tar.gz

# Preview, then remove old history archives and inactive projects' cached page renders.
uv run python -m src.webapp.maintenance prune --older-than-days 90 --archives --pages --dry-run
uv run python -m src.webapp.maintenance prune --older-than-days 90 --archives --pages
```

`prune` prints each candidate and its bytes (and the total bytes removed or expected to be freed). `--dry-run` makes no removals. `--archives` only removes old `modules/history-archive-*` directories using the archive timestamp in the directory name; `--pages` removes `pages/*.png` only when the project's non-cache files have not changed within the cutoff; `--trash` removes old directories from `<projects_root>.parent/trash/`, using the timestamp suffix recorded when the project was moved (and the directory mtime for older names without a timestamp). Current module files, current history, uploads, project metadata and audit logs are never pruned. Cached pages are regenerated by `ProjectStore.page_png` on the next view.

Schedule backups and cleanup with the host scheduler, for example with cron (use a writable backup location and adjust retention to your policy). Set `FA_OUTPUT_DIR` on each command: cron does not inherit the service's environment. An existing empty projects root is valid for a fresh install. Backup refuses to overwrite an existing archive when the snapshot contains no projects; pass `--allow-empty` only when an empty replacement is intentional.

```cron
15 2 * * * cd /opt/furniture-assistant && FA_OUTPUT_DIR=/var/lib/furniture-assistant uv run python -m src.webapp.maintenance backup --output /var/backups/furniture-projects-$(date +\%F).tar.gz
30 3 * * 0 cd /opt/furniture-assistant && FA_OUTPUT_DIR=/var/lib/furniture-assistant uv run python -m src.webapp.maintenance prune --older-than-days 90 --archives --pages --trash
```

Backups cover the complete `<FA_OUTPUT_DIR>/webapp/` state tree—active `projects/` and sibling `trash/`—by copying each project while holding the same cross-process file-based `ProjectStore.lock` used by the server, then creating the archive from a snapshot. Maintenance can safely run while the app is active. The data directory must be writable only by the service user; pruning checks resolved targets before removal and relies on that ownership boundary to prevent a concurrent untrusted user from swapping a checked path for a symlink. Configuration (`config/`), `users.yaml` and the session secret must be backed up separately. `--include-pages` includes PNG caches when desired.

## Audit log integrity

Each new `audit.jsonl` record includes `prev_hash` and `hash`. The hash is SHA-256 of the entry, including `prev_hash`, serialized as canonical UTF-8 JSON (sorted keys, no whitespace). The first chained record uses the SHA-256 of any legacy, unhashed prefix as its `prev_hash`; a log without a legacy prefix starts from 64 zeroes. The `audit_chain_since` line number and legacy-prefix hash are persisted in `project.json` when chaining begins, so removing hash fields from a chained log is detected. Legacy-only logs remain explicitly **unverified**; they are not represented as integrity-verified.

Verify a project or every project under the projects root:

```bash
uv run python -m src.webapp.audit verify results/webapp/projects/<project-id>
uv run python -m src.webapp.audit verify results/webapp/projects
```

The command exits `1` and reports the first broken line in each affected log. `/api/projects/{project_id}/activity` continues to return an `ActivityEntry[]`; `/api/projects/{project_id}/activity/integrity` returns `chain_status`, `chain_error`, and `head_hash`. The Activity page displays verified, unverified (legacy-only), or invalid status; malformed records are skipped in the activity table while causing an invalid integrity result. The raw log is downloadable from `/api/projects/{project_id}/audit.jsonl`.

Hash chaining detects edits and removals within the recorded chain, but it cannot detect tail truncation (including deletion of the entire log): the remaining prefix still verifies. The checkpoint lives beside the log in `project.json`; **anyone able to rewrite both files can forge a verified chain**. The only defense against that attacker is an externally retained copy of the latest `head_hash` (or an exported audit checkpoint) outside the project directory. Compare that external head during audit/restore; keep it with separately protected backups.

## Logs

The app writes application logs to stdout/stderr; under systemd, journald captures them (`journalctl -u furniture-assistant -f`). `FA_LOG_FORMAT=text` is the default; set it to `json` for one JSON object per application log line. JSON entries include timestamp, level, logger, message, request ID, project ID when available, and exception details. `FA_LOG_LEVEL` sets the application log threshold (for example `INFO` or `DEBUG`). Uvicorn loggers retain their own handlers and formatting.

Every HTTP response includes `X-Request-ID`. A valid incoming `X-Request-ID` (1–128 ASCII letters, digits, `.`, `_` or `-`) is echoed; otherwise the app generates one. The ID is included in application logs and propagated to analysis/refine task logs. Legacy pipeline runs also use `FA_TRACING_ENABLED`; the web app instruments PydanticAI agents using the selected tracing backend when enabled. Keep `FA_TRACING_INCLUDE_BINARY_CONTENT=false` unless drawing images should be attached to trace events.

## Production checklist

- Set `FA_ENV=production` in the process environment; values are case-insensitive (`development` or `production` only), and `.env` cannot select the mode. An invalid value fails startup. Production disables `.env` loading.
- Supply `FA_SESSION_SECRET` through a protected process environment / credential source. Startup fails if it is missing.
- Set `FA_USERS_FILE` to a protected users file with at least one account.
- Set `FA_PUBLIC_URL=https://…` when the public endpoint uses HTTPS. This defaults the cookie to `Secure`; `FA_COOKIE_SECURE` can explicitly override it using an accepted boolean spelling.
- Keep the app behind TLS termination and bind uvicorn to loopback. Several workers on one host are fine (`--workers N`); several hosts need a shared filesystem with working `flock` (see [Process and host constraints](#process-and-host-constraints)).
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

Replace the placeholder session secret with a protected credential source before deployment. Multiple workers are supported on one host. Multiple hosts require a shared projects filesystem with working cross-host `flock` and atomic rename semantics (see [Process and host constraints](#process-and-host-constraints)). `OPENAI_API_KEY` must also be injected through the service environment or credential mechanism; production never reads it from `.env`.
