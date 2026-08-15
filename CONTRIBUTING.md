# Contributing

Thanks for taking a look. This is a small project — issues and pull requests
are welcome, and there is no heavyweight process.

## Getting set up

You need Python 3.12+ and a working Docker daemon.

```bash
git clone https://github.com/<owner>/vibeprod.git
cd vibeprod
cp .env.example .env        # add at least one provider key
./run.sh                    # creates .venv, installs deps, serves on :8000
```

`run.sh` runs the broker directly on the host. `docker compose up --build` runs
it in a container instead; both store state in `./data`.

Read [SECURITY.md](SECURITY.md) before you expose the port anywhere — the
broker has no authentication and drives the Docker daemon.

## Running the tests

```bash
./.venv/bin/python -m pytest
```

The suite is a smoke test: it boots the app against a temporary database and
checks that the API answers. It does not need Docker or a provider key. Please
keep it that way — tests that require credentials cannot run in CI.

## Layout

| Path | What lives there |
|---|---|
| `app/main.py` | FastAPI app, lifespan, startup wiring |
| `app/api/` | HTTP routers, one module per resource |
| `app/session_manager.py` | Session lifecycle: create → container → opencode session |
| `app/docker_runner.py` | Everything that talks to the Docker SDK |
| `app/render.py` | Builds `opencode.json` + agent/skill files in a worker's workspace |
| `app/streamer.py` | Relays worker SSE into WebSockets, persists compact events |
| `app/guardian_mcp.py` | The MCP server the operator agent uses to configure Vibeprod |
| `app/static/` | The whole frontend: one HTML file, one JS file, Tailwind via CDN |
| `worker/`, `mcp/` | Dockerfiles for the worker image and bundled MCP services |

The frontend is deliberately dependency-free — no build step, no framework. If
you are adding UI, extend `app/static/app.js` in the same style rather than
introducing a bundler.

## Pull requests

- Keep the diff focused; unrelated cleanups are easier to review separately.
- Match the surrounding style. The codebase is plain, comment-light Python.
- If you change behaviour, update the README (both `README.md` and
  `README.en.md`, or say in the PR that you could only do one).
- New dependencies need a reason — `requirements.txt` is intentionally short.

## Reporting bugs

Include your OS, Python version, whether you run via `run.sh` or Compose, and
the broker log around the failure. If a worker container is involved,
`docker logs <container>` usually has the real error.

Security problems go through the private channel described in
[SECURITY.md](SECURITY.md), not the public issue tracker.
