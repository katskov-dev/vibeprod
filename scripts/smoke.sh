#!/usr/bin/env bash
# E2E-проверка деплоя Vibeprod: health → login → создать сессию → воркер
# поднялся → (опционально) ответ LLM → статус.
#
# Использование:
#   bash scripts/smoke.sh [BASE_URL]
#
# Переменные:
#   SMOKE_SKIP_LLM=1   не ждать ответа LLM (для CI без реальных ключей)
#   SMOKE_TIMEOUT      сек на поднятие воркера (по умолчанию 300)
#   VIBEPROD_LOGIN / VIBEPROD_PASSWORD — берутся из окружения или .env
set -uo pipefail
cd "$(dirname "$0")/.."

# Креды: окружение → .env (compose читает .env сам)
if [ -f .env ]; then
  # shellcheck disable=SC1091
  set -a; source .env; set +a
fi

BASE="${1:-http://127.0.0.1:${VIBEPROD_PORT:-8000}}"
BASE="${BASE%/}"
SMOKE_TIMEOUT="${SMOKE_TIMEOUT:-300}"
COOKIE_JAR="$(mktemp)"
SESSION_ID=""
trap 'rm -f "$COOKIE_JAR"; if [ -n "$SESSION_ID" ]; then curl -fsS -b "$COOKIE_JAR" -X DELETE "$BASE/api/sessions/$SESSION_ID" >/dev/null 2>&1 || true; fi' EXIT

step() { printf '==> %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

step "1/5 health: $BASE/api/health"
HEALTH=""
for _ in $(seq 1 30); do
  HEALTH="$(curl -fsS "$BASE/api/health" 2>/dev/null)" && break
  sleep 2
done
[ -n "$HEALTH" ] || fail "брокер не отвечает. Смотрите: docker compose ps; docker compose logs broker"
echo "$HEALTH"
echo "$HEALTH" | grep -q '"docker":true' || fail "docker-демон недоступен из брокера — проверьте mount /var/run/docker.sock"
echo "$HEALTH" | grep -q '"s3":true' || fail "MinIO недоступен — проверьте VIBEPROD_S3_ENDPOINT и MINIO_ROOT_USER/PASSWORD"

AUTH_STATE="$(curl -fsS "$BASE/api/auth")"
if echo "$AUTH_STATE" | grep -q '"enabled":true'; then
  step "2/5 login (авторизация включена)"
  [ -n "${VIBEPROD_LOGIN:-}" ] && [ -n "${VIBEPROD_PASSWORD:-}" ] \
    || fail "авторизация включена, но VIBEPROD_LOGIN/PASSWORD не заданы в окружении/.env"
  curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" -H "Content-Type: application/json" \
    -d "{\"login\":\"$VIBEPROD_LOGIN\",\"password\":\"$VIBEPROD_PASSWORD\"}" \
    "$BASE/api/login" >/dev/null || fail "login не удался — проверьте креды"
  curl -fsS -b "$COOKIE_JAR" -c "$COOKIE_JAR" "$BASE/api/agents" >/dev/null \
    || fail "cookie-авторизация не сработала"
else
  step "2/5 login — авторизация выключена, пропускаю"
fi

step "3/5 создаю сессию"
AGENT_ID="$(curl -fsS -b "$COOKIE_JAR" "$BASE/api/agents" | python3 -c "
import json, sys
agents = json.load(sys.stdin)
if not agents:
    raise SystemExit('нет ни одного агента')
default = next((a for a in agents if a.get('is_default') == 1), agents[0])
print(default['id'])
")" || fail "не удалось получить список агентов: $AGENT_ID"
SESSION="$(curl -fsS -b "$COOKIE_JAR" -H "Content-Type: application/json" \
  -d "{\"agent_id\": $AGENT_ID, \"title\": \"smoke\", \"prompt\": \"Ответь одним словом: ok\"}" \
  "$BASE/api/sessions")" || fail "не удалось создать сессию"
SESSION_ID="$(echo "$SESSION" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")"
echo "session: $SESSION_ID"

step "4/5 жду воркер (opencode serve в контейнере, до ${SMOKE_TIMEOUT}s)"
DEADLINE=$(( $(date +%s) + SMOKE_TIMEOUT ))
INFRA_OK=0
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  ROW="$(curl -fsS -b "$COOKIE_JAR" "$BASE/api/sessions/$SESSION_ID")" || { sleep 2; continue; }
  STATUS="$(echo "$ROW" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status'))")"
  HOST_PORT="$(echo "$ROW" | python3 -c "import json,sys; print(json.load(sys.stdin).get('host_port') or '')")"
  # host_port брокер записывает только ПОСЛЕ успешного wait_healthy воркера —
  # значит, брокер реально достучался до 127.0.0.1:<port> (проверка сети).
  if [ -n "$HOST_PORT" ]; then
    echo "воркер поднялся, брокер до него достучался: host_port=$HOST_PORT (статус: $STATUS)"
    INFRA_OK=1
    break
  fi
  if [ "$STATUS" = "failed" ]; then
    echo "$ROW" | python3 -c "import json,sys; print('ошибка сессии:', json.load(sys.stdin).get('error'))" >&2
    fail "сессия упала до поднятия воркера — сеть брокер↔воркер (network_mode: host?), docker-демон или образ"
  fi
  sleep 3
done
[ "$INFRA_OK" = 1 ] || fail "воркер не поднялся за ${SMOKE_TIMEOUT}s (статус: $STATUS)"

HAVE_KEY=0
for key in ANTHROPIC_API_KEY OPENAI_API_KEY DEEPSEEK_API_KEY GOOGLE_GENERATIVE_AI_API_KEY; do
  [ -n "${!key:-}" ] && HAVE_KEY=1
done
if [ "${SMOKE_SKIP_LLM:-0}" = "1" ]; then
  step "5/5 ответ LLM — пропущен (SMOKE_SKIP_LLM=1)"
elif [ "$HAVE_KEY" != 1 ]; then
  step "5/5 ответ LLM — пропущен (ни одного API-ключа в окружении/.env)"
else
  step "5/5 жду ответ LLM"
  LLM_DEADLINE=$(( $(date +%s) + SMOKE_TIMEOUT ))
  REPLY=""
  while [ "$(date +%s)" -lt "$LLM_DEADLINE" ]; do
    MSGS="$(curl -fsS -b "$COOKIE_JAR" "$BASE/api/sessions/$SESSION_ID/messages")" || { sleep 3; continue; }
    REPLY="$(echo "$MSGS" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for m in reversed(data.get('result') or []):
    info = m.get('info') or {}
    if info.get('role') != 'assistant':
        continue
    if info.get('error'):
        print('ERROR:' + str(info['error'])[:300]); break
    if (info.get('time') or {}).get('completed'):
        print(' '.join(p.get('text', '') for p in m.get('parts', []) if p.get('type') == 'text')[:300]); break
print('', end='')
")"
    case "$REPLY" in
      ERROR:*) echo "$REPLY" >&2; fail "LLM вернул ошибку — проверьте ключи на странице «Провайдеры»" ;;
      "") sleep 3 ;;
      *) echo "ответ: $REPLY"; break ;;
    esac
  done
  [ -n "$REPLY" ] || fail "LLM не ответил за отведённое время"
fi

step "итог: сессия $SESSION_ID (статус: $(curl -fsS -b "$COOKIE_JAR" "$BASE/api/sessions/$SESSION_ID" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status'))"))"
echo "OK: деплой работает"
