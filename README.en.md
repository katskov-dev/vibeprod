# Vibeprod

**A self-hosted control plane for [opencode](https://github.com/sst/opencode) agents.**
Every session gets its own Docker container. Runs on your machine, with your API keys, under your rules.

[![CI](https://github.com/katskov-dev/vibeprod/actions/workflows/ci.yml/badge.svg)](https://github.com/katskov-dev/vibeprod/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

[Русская версия](README.md) · [Landing page](https://katskov-dev.github.io/vibeprod/)

![Sessions](docs/screenshots/sessions.png)

---

## What this is

opencode is an excellent terminal coding agent. Vibeprod is the layer around it
that a *team* needs: a web UI over many sessions at once, agents defined once
and reused, cron schedules, inbound and outbound webhooks, a Telegram channel,
and a reusable MCP catalog — all in one FastAPI process with a SQLite file next
to it.

The whole thing is roughly 6,000 lines of Python plus one dependency-free
frontend. There is no build step, no message queue, no Kubernetes.

**What makes it different from the hosted agent products:** the schedules,
webhooks and chat channels are *built in and self-hosted*. You do not need a
cloud tier to have an agent that runs every weekday at 09:00 and posts the
result to Telegram — and no code or API key ever leaves your machine.

> [!WARNING]
> **The broker has no authentication and it drives the Docker daemon.** Anyone
> who can reach its port can execute code as root on the host. Compose binds it
> to `127.0.0.1` for exactly this reason. Read [SECURITY.md](SECURITY.md) before
> putting it on a server.

## Quick start

Requires Docker and Python 3.11+ (or just Docker).

```bash
git clone https://github.com/katskov-dev/vibeprod.git
cd vibeprod
cp .env.example .env     # add at least one provider key
./run.sh                 # → http://localhost:8000
```

Or in a container:

```bash
docker compose up --build
```

The first start pulls the pinned `ghcr.io/anomalyco/opencode` image (version
fixed in compose.yaml and worker/Dockerfile) and builds the worker image on
top of it — opencode plus a light coding-agent toolkit: python3/pip, curl, jq,
git, ripgrep, bash, make, zip, openssh (no compilers, to keep the image lean).

Open the UI, describe what you need on the home screen, and the **operator
agent** will set the project up for you — it creates agents, attaches MCP
servers and skills, adds providers, webhooks and schedules, all through a
dedicated MCP server inside the broker.

## Deploy

Server requirements: Linux, Docker + Docker Compose v2, git, `curl`. Deploy
from the repository — so fixes can be rolled back and merged back upstream:

```bash
git clone https://github.com/katskov-dev/vibeprod.git /srv/vibeprod
cd /srv/vibeprod
bash scripts/setup.sh
```

`setup.sh` checks Docker, generates `.env` with random MinIO and UI passwords,
asks for LLM keys (at least one), runs `docker compose up -d --build` and
prints the URL and credentials. Then verify the whole deployment:

```bash
bash scripts/smoke.sh    # health → login → session → worker → LLM reply → status
```

Mind the networking: the broker runs with `network_mode: host` in compose, and
this is **required**. The broker reaches workers at `127.0.0.1:<host-port>`,
but inside a bridge network `127.0.0.1` is the broker's own loopback, not the
host — every session fails with "opencode serve не поднялся". For the same
reason (no container DNS in host mode) MinIO is published on the host loopback
(`127.0.0.1:9000`). With host networking, the broker's port is just a uvicorn
bind: `setup.sh` enables `VIBEPROD_BIND=0.0.0.0` together with UI auth — read
[SECURITY.md](SECURITY.md) first (without auth it's a root shell on the host).
Check readiness with `docker compose ps` (both services have healthchecks) or
`docker compose up --wait`.

Updating: `cd /srv/vibeprod && git pull && docker compose up -d --build`
(the worker image rebuilds automatically on the next session when
`worker/Dockerfile` changed).

### Common deployment errors

| Symptom | Cause | Fix |
|---|---|---|
| Sessions fail with "opencode serve не поднялся" | Broker can't reach the worker port on `127.0.0.1` — `network_mode: host` missing | Use compose.yaml as-is (`network_mode: host` is mandatory); `docker compose config` shows what actually deploys |
| Probe containers fail with "bind source path does not exist" | `VIBEPROD_HOST_DATA_DIR` not set: the Docker daemon resolves bind sources on the host, not inside the broker | Compose sets `VIBEPROD_HOST_DATA_DIR=${PWD}/data`; outside compose, set it explicitly |
| Provider catalog empty / "Check" fails (502) | MinIO or Docker unreachable from the broker | `curl http://127.0.0.1:9000/minio/health/live` on the host; `VIBEPROD_S3_ENDPOINT` must be `http://127.0.0.1:9000` (compose default) |
| `docker compose ps` shows broker unhealthy | Docker daemon or MinIO unreachable from the broker | `docker info` on the host; verify the `/var/run/docker.sock` mount; `curl …/api/health` shows the `docker`/`s3` flags |
| Sessions fail after an update, but used to work | LLM keys missing | `/api/health` only covers infrastructure; keys live in `.env`/UI. `scripts/smoke.sh` without `SMOKE_SKIP_LLM=1` catches this |

## How it works

```
Browser ⇄ WebSocket ⇄ FastAPI broker (SQLite)
                          │ Docker SDK
                          ▼
     one `opencode serve` container per session
     (isolated workspace, volume for history,
      SSE /event relayed into the WebSocket)
```

A **session** is a container plus an opencode session. Conversation history
lives in a Docker volume (`vibeprod-oc-<id>`), so a worker can be restarted
without losing the thread. After the idle TTL the container is killed and the
session is marked `expired`; its data stays in the database until you delete it.

Each worker listens on a random host port behind HTTP basic auth with a
per-session random token.

## Features

### Agents, skills and MCP

![Agents](docs/screenshots/agents.png)

Agents are rows in SQLite — model, mode, temperature, permissions, system
prompt — plus attached MCP servers and skills. When a session starts,
`render.py` materialises a native `opencode.json`, `.opencode/agent/*.md` and
`.opencode/skills/*/SKILL.md` into the worker's workspace. Nothing proprietary:
what the worker sees is stock opencode configuration.

![MCP catalog](docs/screenshots/mcp-catalog.png)

The **MCP catalog** holds reusable servers of two kinds. *Generic* ones are
ordinary local (command) or remote (URL) servers. *Service* ones are Docker
containers on the `vibeprod-mcp` network — the bundled **playwright** service
gives any agent a real browser, and starts automatically when a session needs it.
Attaching one to an agent is a single click.

### Automation: schedules, webhooks, Telegram

![Schedules](docs/screenshots/schedules.png)

**Schedules** are cron expressions with a timezone. Each firing creates an
ordinary session tagged `schedule`, and the outcome is recorded in
`schedule_runs`.

**Webhooks** let external systems start an agent:

```bash
curl -X POST http://localhost:8000/api/webhooks/pr-review/run \
     -H 'X-Webhook-Secret: ...' \
     -H 'Content-Type: application/json' \
     -d '{"prompt": "Review the diff in PR #482"}'
```

Add `?wait=<seconds>` to block until the run finishes and get the result in the
response instead of polling.

**Outgoing webhooks** push broker events to your systems (Automation →
"Outgoing"). Subscribe a URL to events like `session.completed`,
`session.failed`, `schedule.fired` or `webhook.received` and the broker will
POST to it:

```json
{
  "event": "session.completed",
  "timestamp": "2026-08-15T09:00:00Z",
  "data": {"id": "...", "title": "Review PR #482", "status": "completed", "result_text": "…"}
}
```

Requests carry `X-Vibeprod-Event` and `X-Vibeprod-Delivery` headers and, if you
set a secret, an HMAC-SHA256 signature in `X-Vibeprod-Signature: sha256=<hex>`
over the raw body. Deliveries retry with backoff (1s → 5s → 15s → 60s → 300s)
on network errors, 429 and 5xx; every attempt lands in the per-webhook delivery
log with the response code and error.

![Channels](docs/screenshots/channels.png)

**Telegram** runs inside the broker on long polling — no extra dependency, no
public URL. Configure the bot token and allowed user IDs in the UI. The first
message opens a session, later ones continue it, and the agent's reply is
streamed by editing the bot's message in place. Commands: `/agents`, `/agent N`,
`/new`, `/abort`, `/status`, `/link`, `/chatid`. Set a notification chat (see
`/chatid`) to receive summaries of scheduled and webhook runs — on every run or
only on errors.

Every agent also gets built-in Vibeprod tools (a `vibeprod` remote-MCP inside
each session): `telegram_send` — message the user on Telegram,
`telegram_send_file` — send a file (from the worker workspace or as text),
`telegram_info` — channel status. Handy for scheduled runs: the agent finishes a
job and delivers the result and files to the chat itself.

### Live sessions

![Chat](docs/screenshots/chat.png)

The broker consumes the worker's SSE stream and fans it out over
`WS /ws/sessions/{id}`. Tool calls, reasoning blocks and todo lists render as
they happen; compact events go to SQLite and the full transcript is stored when
the session goes idle.

### Providers

![Providers](docs/screenshots/providers.png)

Provider keys are managed in the UI and take precedence over `*_API_KEY` in the
broker's environment. **Check** spins up a short-lived probe container with the
same opencode image and your key, verifies the provider registers, lists its
models and makes a real generation request — the same path a worker will take.

## Comparison

Honest positioning: Vibeprod is a small, self-hosted project, not a competitor to
funded platforms on breadth or maturity. It wins on one axis — *self-hosted
automation without a cloud tier* — and loses on another — *it has no
authentication or multi-user support at all*.

| | **Vibeprod** | [OpenHands](https://github.com/OpenHands/OpenHands) | [opencode](https://github.com/sst/opencode) | [Goose](https://github.com/block/goose) | Devin · Jules · Codex cloud · Cursor agents |
|---|---|---|---|---|---|
| License | MIT | MIT | MIT | Apache-2.0 | Proprietary |
| Self-hosted | Only option | Yes (+ managed cloud) | Yes | Yes | No |
| Web UI over many sessions | ✅ | ✅ | ❌ (TUI + serve API) | ❌ (desktop/CLI) | ✅ vendor-hosted |
| Container per session | ✅ | ✅ | ❌ runs on host | ❌ runs on host | ✅ vendor sandbox |
| Cron schedules | ✅ built in | ☁️ cloud tier | ❌ | ❌ | ⚠️ varies |
| Inbound webhooks | ✅ built in | ☁️ cloud tier | ❌ | ❌ | ⚠️ varies |
| Outbound webhooks | ✅ built in | ⚠️ varies | ❌ | ❌ | ⚠️ varies |
| Chat channel (Telegram) | ✅ | ❌ | ❌ | ❌ | ❌ |
| MCP support | ✅ + shared catalog | ✅ | ✅ | ✅ | ⚠️ varies |
| Agent that configures the platform | ✅ guardian MCP | ❌ | ❌ | ❌ | ❌ |
| Bring your own key | ✅ | ✅ | ✅ | ✅ | ❌ subscription |
| Git / pull-request workflow | ⚠️ via agent + git | ✅ first-class | ✅ first-class | ✅ | ✅ first-class |
| Auth, multi-user, RBAC | ❌ **none** | ✅ enterprise | n/a | n/a | ✅ |
| Maturity | 🌱 early | Large, funded | Very large | Linux Foundation | Commercial |

**Pick Vibeprod if** you want agents on your own hardware, triggered by cron,
webhooks and chat, with per-session isolation and keys that never leave the box.

**Pick something else if** you need multi-user access control, a hosted service
with an SLA, or a mature pull-request review workflow.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `VIBEPROD_DATA_DIR` | `./data` | SQLite + workspaces (path inside the broker) |
| `VIBEPROD_HOST_DATA_DIR` | `= VIBEPROD_DATA_DIR` | Same directory as the Docker daemon sees it. Needed when the broker itself is containerised |
| `VIBEPROD_OPENCODE_IMAGE` | `vibeprod-opencode:latest` | Worker image, built from `worker/` on first run |
| `VIBEPROD_WORKER_BUILD_DIR` | `./worker` | Build context for the worker image |
| `VIBEPROD_IDLE_TTL_MIN` | `120` | Idle minutes before a worker is killed |
| `VIBEPROD_PORT` | `8000` | Broker port |
| `VIBEPROD_BIND` | `127.0.0.1` | Uvicorn bind address; with `network_mode: host` this *is* the port exposure. `0.0.0.0` only together with `VIBEPROD_LOGIN`/`VIBEPROD_PASSWORD` |
| `VIBEPROD_TZ` | `Europe/Moscow` | Timezone for cron schedules |
| `VIBEPROD_GUARDIAN_URL` | `http://host.docker.internal:<port>/guardian/mcp` | Guardian MCP URL as workers see it |
| `VIBEPROD_LOGIN` · `VIBEPROD_PASSWORD` | — | Login/password for the web UI. Both empty — no login required |
| `TELEGRAM_BOT_TOKEN` · `TELEGRAM_ALLOWED_USERS` · `TELEGRAM_WEB_URL` | — | First-boot fallback if nothing is configured in the UI |
| `*_API_KEY` | — | Passed into workers |

See [.env.example](.env.example) for the annotated version.

## API

```
GET/POST   /api/projects              PUT/DELETE /api/projects/{id}
GET/POST   /api/agents                PUT/DELETE /api/agents/{id}
POST/PUT/DELETE /api/agents/{id}/mcp[/{mid}]     PUT /api/agents/{id}/skills
GET/POST   /api/skills                PUT/DELETE /api/skills/{id}
GET/POST   /api/providers             PUT/DELETE /api/providers/{id}
POST       /api/providers/{id}/check          probe container: register + models + test call
GET/POST   /api/mcp-catalog           PUT/DELETE /api/mcp-catalog/{id}
POST       /api/mcp-catalog/{id}/start|stop   docker services
POST       /api/mcp-catalog/{id}/attach       {agent_id}
GET/POST   /api/webhooks              PUT/DELETE /api/webhooks/{id}
POST       /api/webhooks/{slug}/run           body {prompt?, title?}, X-Webhook-Secret, ?wait=<sec>
GET/POST   /api/out-webhooks          PUT/DELETE /api/out-webhooks/{id}
GET        /api/out-webhooks/events
POST       /api/out-webhooks/{id}/test        send webhook.test to the URL
GET        /api/out-webhooks/{id}/deliveries  delivery log (status, HTTP code, attempts)
POST       /api/out-webhooks/{id}/deliveries/{did}/retry
GET        /api/channels
GET/PUT/DELETE /api/telegram          POST /api/telegram/test {token}
GET/POST   /api/sessions              POST /api/sessions/{id}/prompt|abort|restart
GET        /api/sessions/{id}/messages        DELETE /api/sessions/{id}
WS         /ws/sessions/{id}
GET/POST   /api/schedules             PUT/DELETE /api/schedules/{id}
POST       /api/schedules/{id}/run-now        GET /api/schedules/{id}/runs
POST       /guardian/mcp                      JSON-RPC over streamable HTTP, bearer secret required
```

Most collection endpoints accept `?project_id=` to scope the result.

## Troubleshooting

**The WebSocket will not connect, or local requests return 503.**
The broker talks to workers over `127.0.0.1`. If a system-wide HTTP proxy is
enabled (`scutil --proxy` on macOS), Python clients and the browser will route
loopback traffic through it. Add `127.0.0.1,localhost` to `no_proxy` and to the
system proxy exception list.

**`Model not found: provider/model`.**
opencode only registers a provider's models when an API key is present. Check
that the key reached the worker — the **Check** button on the Providers page
runs exactly that path and shows the model list it got back.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Tests: `pytest -q` (no Docker or API key
needed).

## License

MIT — see [LICENSE](LICENSE). © 2026 Pavel Katskov.

Built on [opencode](https://github.com/sst/opencode) by Anomaly Innovations,
also MIT.
