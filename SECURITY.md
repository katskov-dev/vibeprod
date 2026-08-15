# Security Policy

## Threat model — read this before you deploy

Vibeprod is designed to run **on a trusted machine, reachable only by you**:
your laptop, or a private host behind a VPN. It is *not* hardened for exposure
to the public internet, and two properties make that dangerous:

1. **The broker has no authentication.** Every `/api/*` endpoint and the web UI
   are open to anyone who can reach the port. There is no login, no session, no
   API key. (The one exception is `/guardian/mcp`, which requires a bearer
   secret.)
2. **The broker controls the Docker daemon.** `compose.yaml` mounts
   `/var/run/docker.sock` so the broker can start worker containers. Access to
   the Docker socket is equivalent to root on the host.

Combined: **anyone who can reach the broker's port can execute code as root on
the host machine.** Treat the port the way you would treat an unauthenticated
root shell.

For this reason the bundled `compose.yaml` publishes the port on `127.0.0.1`
only. Do not change that to `0.0.0.0` unless you have read the next section.

## Deploying on a remote host

If you need Vibeprod on a server, put authentication in front of it and never
publish the port directly:

- Keep the container bound to loopback (`127.0.0.1:8000:8000` — the default).
- Terminate TLS and authenticate in a reverse proxy (nginx/Caddy with basic
  auth, oauth2-proxy, Cloudflare Access, Tailscale Serve — anything that
  rejects anonymous requests).
- Or skip public exposure entirely: reach it over a VPN or an SSH tunnel
  (`ssh -L 8000:127.0.0.1:8000 user@host`).
- Webhook endpoints (`POST /api/webhooks/{slug}/run`) are the one thing you may
  want reachable from outside. Expose *only* that path through the proxy, and
  set a secret on each webhook (checked via the `X-Webhook-Secret` header).

## Outgoing webhooks

Outgoing webhooks make the broker send HTTP POSTs to URLs you configure. Two
things to keep in mind:

- **SSRF:** the broker will happily POST to any URL reachable from the host —
  including `127.0.0.1`, `169.254.169.254` (cloud metadata) and other internal
  services. Since anyone with access to the broker can add an outgoing webhook,
  that is effectively a port-scan/request-forgery primitive. Treat broker
  access as admin access (see the threat model above).
- **Signatures:** set a secret on each outgoing webhook and verify the
  `X-Vibeprod-Signature` header (HMAC-SHA256 over the raw request body) on the
  receiving side, so your endpoint can tell real Vibeprod events from spoofed
  ones. Secrets are stored in plaintext in `data/vibeprod.db`, like provider
  keys.

## What agents can do

Each session runs in its own container, and opencode permission prompts are
**auto-approved** — the agent does not ask before writing files or running
shell commands. The container is the security boundary, not the prompt.

Consequences to keep in mind:

- An agent can run arbitrary code inside its own container. That container has
  network access and any `*_API_KEY` you configured for its provider.
- Workers reach the broker through `host.docker.internal`, so they can also
  reach other services listening on your host.
- Prompts, webhook bodies, Telegram messages, and web pages an agent reads are
  all **untrusted input**. A malicious payload can steer the agent. Do not point
  agents at untrusted content while giving them credentials that matter.
- Mount only repositories you are willing to let an agent modify.

## Secrets

- Provider API keys are stored **in plaintext** in `data/vibeprod.db`, and the
  guardian bearer secret alongside them. The whole `data/` directory is
  gitignored — keep it that way, and treat backups of it as secret material.
- `.env` is gitignored. Never commit real keys; `.env.example` is the template.
- Telegram bot tokens live in the same database (`telegram_config`).

## Reporting a vulnerability

Please do **not** open a public issue for a security problem.

Report it privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository (Security → Report a vulnerability).

This is a hobby-scale project with no SLA. Expect a first response within about
a week. Please include reproduction steps and the impact you observed.

## Supported versions

Only the latest commit on `main` receives fixes. There are no backports.
