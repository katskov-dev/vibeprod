#!/usr/bin/env bash
# Настройка и первый запуск Vibeprod на сервере.
# Требования: docker + docker compose v2 + git (репозиторий клонирован).
#
# Делает: проверяет docker → создаёт .env со случайными секретами → спрашивает
# LLM-ключи → docker compose up --build → ждёт healthcheck → печатает URL и креды.
set -euo pipefail
cd "$(dirname "$0")/.."

err() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }
warn() { printf 'ПРЕДУПРЕЖДЕНИЕ: %s\n' "$*" >&2; }

command -v docker >/dev/null 2>&1 || err "docker не установлен — установите docker и docker compose v2"
docker info >/dev/null 2>&1 || err "docker-демон не запущен или нет прав (sudo usermod -aG docker $(id -un) && перелогиньтесь)"
docker compose version >/dev/null 2>&1 || err "docker compose v2 не найден"

if [ -f .env ]; then
  echo ".env уже существует — использую его (удалите файл, чтобы сгенерировать заново)."
else
  echo "Генерирую .env со случайными секретами..."
  gen_pass() {
    openssl rand -hex 24 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(24))"
  }
  MINIO_ROOT_USER="minioadmin"
  MINIO_ROOT_PASSWORD="$(gen_pass)"
  VIBEPROD_LOGIN="admin"
  VIBEPROD_PASSWORD="$(gen_pass)"
  {
    echo "# Сгенерировано scripts/setup.sh $(date +%F). .env в .gitignore — не коммитьте."
    echo "MINIO_ROOT_USER=$MINIO_ROOT_USER"
    echo "MINIO_ROOT_PASSWORD=$MINIO_ROOT_PASSWORD"
    echo "VIBEPROD_S3_ACCESS_KEY=$MINIO_ROOT_USER"
    echo "VIBEPROD_S3_SECRET_KEY=$MINIO_ROOT_PASSWORD"
    echo "VIBEPROD_S3_ENDPOINT=http://127.0.0.1:9000"
    echo "# Наружу + авторизация обязательна (см. SECURITY.md)"
    echo "VIBEPROD_BIND=0.0.0.0"
    echo "VIBEPROD_PORT=8000"
    echo "VIBEPROD_LOGIN=$VIBEPROD_LOGIN"
    echo "VIBEPROD_PASSWORD=$VIBEPROD_PASSWORD"
    echo "VIBEPROD_TZ=${VIBEPROD_TZ:-Europe/Moscow}"
    echo "VIBEPROD_IDLE_TTL_MIN=120"
  } > .env
  echo "Секреты записаны в .env (MinIO-пароль и пароль UI — случайные)."
fi

# shellcheck disable=SC1091
set -a; source .env; set +a

# LLM-ключи: просим только отсутствующие в окружении; новые дописываем в .env.
KEYS="ANTHROPIC_API_KEY OPENAI_API_KEY DEEPSEEK_API_KEY GOOGLE_GENERATIVE_AI_API_KEY"
for key in $KEYS; do
  if [ -n "${!key:-}" ]; then
    echo "$key: уже задан, пропускаю."
    continue
  fi
  read -r -p "$key (Enter — пропустить): " val
  if [ -n "$val" ]; then
    grep -q "^$key=" .env 2>/dev/null || echo "$key=$val" >> .env
    export "$key=$val"
  fi
done

# shellcheck disable=SC1091
set -a; source .env; set +a

has_key=0
for key in $KEYS; do
  [ -n "${!key:-}" ] && has_key=1
done
[ "$has_key" = 1 ] || warn "не задан ни один LLM-ключ — сессии не запустятся. Ключи можно добавить позже в UI (Провайдеры) или в .env и перезапустить: docker compose up -d"

echo "Собираю и поднимаю контейнеры (первый раз качает образы — это небыстро)..."
docker compose up -d --build

echo "Жду готовности брокера (healthcheck: docker-демон + MinIO)..."
healthy=0
for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${VIBEPROD_PORT:-8000}/api/health" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 2
done
if [ "$healthy" != 1 ]; then
  echo "Брокер не поднялся. Логи:" >&2
  docker compose logs --tail=60 broker >&2
  err "см. README → Деплой → таблица типовых ошибок"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$IP" ] || IP="<IP сервера>"
echo ""
echo "Готово: http://$IP:${VIBEPROD_PORT:-8000}"
echo "Логин: ${VIBEPROD_LOGIN:--}   Пароль: ${VIBEPROD_PASSWORD:--}"
echo "MinIO-консоль (по SSH-туннелю): ssh -L 9001:127.0.0.1:9001 $(id -un)@$IP"
echo "Проверить деплой целиком: bash scripts/smoke.sh"
