# Vibeprod

**Самостоятельно размещаемая панель управления агентами [opencode](https://github.com/sst/opencode).**
Каждый сеанс — свой docker-контейнер. Работает на вашей машине, на ваших ключах, по вашим правилам.

[![CI](https://github.com/katskov-dev/vibeprod/actions/workflows/ci.yml/badge.svg)](https://github.com/katskov-dev/vibeprod/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

[English version](README.md) · [Лендинг](https://katskov-dev.github.io/vibeprod/)

![Сессии](docs/screenshots/sessions.png)

---

## Что это

opencode — отличный терминальный агент. Vibeprod — слой вокруг него, который
нужен, когда агентов становится больше одного: веб-интерфейс над всеми сеансами
сразу, агенты, описанные один раз и переиспользуемые, cron-расписания, входящие
вебхуки, канал в Telegram и общий каталог MCP. Всё это — один процесс FastAPI и
файл sqlite рядом с ним.

Внутри примерно 6 000 строк питона и фронтенд без единой зависимости. Ни сборки,
ни очереди сообщений, ни кубернетеса.

**Чем отличается от облачных агентских продуктов:** расписания, вебхуки и
мессенджер-каналы здесь встроены и работают локально. Чтобы агент запускался в
09:00 по будням и присылал результат в Telegram, не нужен облачный тариф — и ни
код, ни ключи не покидают вашу машину.

> [!WARNING]
> **У брокера нет аутентификации, и он управляет докер-демоном.** Любой, кто
> дотянется до его порта, может выполнить код с правами root на хосте. Именно
> поэтому compose публикует порт только на `127.0.0.1`. Прочитайте
> [SECURITY.md](SECURITY.md), прежде чем ставить это на сервер.

## Быстрый старт

Нужен docker и Python 3.11+ (или только docker).

```bash
git clone https://github.com/katskov-dev/vibeprod.git
cd vibeprod
cp .env.example .env     # впишите хотя бы один ключ провайдера
./run.sh                 # → http://localhost:8000
```

Или в контейнере:

```bash
docker compose up --build
```

Первый запуск подтягивает `ghcr.io/anomalyco/opencode:latest` и собирает поверх
него образ воркера (opencode + git + openssh, чтобы агенты работали с
репозиториями).

Откройте интерфейс, опишите задачу на главном экране — и **агент-оператор**
настроит проект сам: создаст агентов, подключит MCP-серверы и скиллы, добавит
провайдеров, вебхуки и расписания. Делает он это через отдельный MCP-сервер,
живущий внутри брокера.

## Как устроено

```
Браузер ⇄ WebSocket ⇄ FastAPI (брокер, sqlite)
                          │ Docker SDK
                          ▼
     контейнер `opencode serve` на каждый сеанс
     (изолированный workspace, том для истории,
      SSE /event ретранслируется в WebSocket)
```

**Сеанс** = контейнер + сессия opencode. История диалога лежит в docker-томе
`vibeprod-oc-<id>`, поэтому воркер можно перезапустить, не потеряв переписку. По
TTL простоя контейнер убивается, сессия помечается `expired` — данные остаются в
базе до удаления.

Каждый воркер слушает случайный порт хоста под HTTP basic auth со случайным
токеном на сеанс.

## Возможности

### Агенты, скиллы и MCP

![Агенты](docs/screenshots/agents.png)

Агенты — строки в sqlite: модель, mode, temperature, permissions, системный
промпт, плюс привязанные MCP-серверы и скиллы. При старте сеанса `render.py`
собирает нативный `opencode.json`, `.opencode/agent/*.md` и
`.opencode/skills/*/SKILL.md` в workspace воркера. Никакого проприетарного
формата: воркер видит обычный конфиг opencode.

![Каталог MCP](docs/screenshots/mcp-catalog.png)

**Каталог MCP** хранит переиспользуемые серверы двух видов. *generic* — обычные
local (команда) или remote (URL). *service* — docker-контейнеры в сети
`vibeprod-mcp`; встроенный **playwright** даёт любому агенту настоящий браузер и
поднимается автоматически, когда нужен сессии. Добавление к агенту — одна кнопка.

### Автоматизация: расписания, вебхуки, Telegram

![Расписания](docs/screenshots/schedules.png)

**Расписания** — cron-выражения с таймзоной. Каждый запуск порождает обычную
сессию с меткой «расписание», результат пишется в `schedule_runs`.

**Вебхуки** позволяют запускать агента из внешних систем:

```bash
curl -X POST http://localhost:8000/api/webhooks/pr-review/run \
     -H 'X-Webhook-Secret: ...' \
     -H 'Content-Type: application/json' \
     -d '{"prompt": "Проверь diff в PR #482"}'
```

`?wait=<сек>` — дождаться завершения и получить результат прямо в ответе, вместо
опроса.

![Каналы](docs/screenshots/channels.png)

**Telegram** живёт внутри брокера на long-polling — без новых зависимостей и без
публичного URL. Токен бота и разрешённые user id настраиваются в интерфейсе.
Первое сообщение открывает сессию, следующие продолжают её, ответ агента
дописывается правками в сообщение бота. Команды: `/agents`, `/agent N`, `/new`,
`/abort`, `/status`, `/link`.

### Живые сессии

![Чат](docs/screenshots/chat.png)

Брокер читает SSE воркера и рассылает в `WS /ws/sessions/{id}`. Вызовы
инструментов, блоки рассуждений и списки задач появляются по мере выполнения;
компактные события пишутся в sqlite, полный транскрипт сохраняется по
`session.idle`.

### Провайдеры

![Провайдеры](docs/screenshots/providers.png)

Ключи провайдеров задаются в интерфейсе и имеют приоритет над `*_API_KEY` из
окружения брокера. Кнопка **«Проверить»** поднимает короткоживущий
probe-контейнер с тем же образом opencode и вашим ключом, проверяет регистрацию
провайдера, список моделей и делает настоящий тест-запрос — ровно то, что
произойдёт в воркере.

## Сравнение с аналогами

Честно о позиционировании: Vibeprod — небольшой self-hosted проект, а не
конкурент профинансированным платформам по широте и зрелости. Он выигрывает по
одной оси — *автоматизация на своём железе без облачного тарифа* — и проигрывает
по другой: *здесь вообще нет аутентификации и многопользовательского режима*.

| | **Vibeprod** | [OpenHands](https://github.com/OpenHands/OpenHands) | [opencode](https://github.com/sst/opencode) | [Goose](https://github.com/block/goose) | Devin · Jules · Codex cloud · Cursor agents |
|---|---|---|---|---|---|
| Лицензия | MIT | MIT | MIT | Apache-2.0 | Проприетарные |
| Self-hosted | Единственный вариант | Да (+ облако) | Да | Да | Нет |
| Веб-интерфейс над многими сеансами | ✅ | ✅ | ❌ (TUI + serve API) | ❌ (десктоп/CLI) | ✅ у вендора |
| Контейнер на сеанс | ✅ | ✅ | ❌ на хосте | ❌ на хосте | ✅ песочница вендора |
| Cron-расписания | ✅ встроены | ☁️ облачный тариф | ❌ | ❌ | ⚠️ по-разному |
| Входящие вебхуки | ✅ встроены | ☁️ облачный тариф | ❌ | ❌ | ⚠️ по-разному |
| Канал в мессенджере (Telegram) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Поддержка MCP | ✅ + общий каталог | ✅ | ✅ | ✅ | ⚠️ по-разному |
| Агент настраивает саму платформу | ✅ guardian MCP | ❌ | ❌ | ❌ | ❌ |
| Свои ключи (BYOK) | ✅ | ✅ | ✅ | ✅ | ❌ подписка |
| Работа с git и пул-реквестами | ⚠️ через агента и git | ✅ из коробки | ✅ из коробки | ✅ | ✅ из коробки |
| Аутентификация, многопользовательский режим, RBAC | ❌ **нет** | ✅ enterprise | — | — | ✅ |
| Зрелость | 🌱 ранняя | Крупный, с инвестициями | Очень крупный | Linux Foundation | Коммерческие |

**Берите Vibeprod, если** нужны агенты на своём железе, запускаемые по cron,
вебхукам и из чата, с изоляцией по сеансам и ключами, которые никуда не уходят.

**Берите что-то другое, если** нужны разграничение доступа, размещённый сервис с
SLA или зрелый процесс ревью пул-реквестов.

## Настройка

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `VIBEPROD_DATA_DIR` | `./data` | sqlite + workspaces (путь внутри брокера) |
| `VIBEPROD_HOST_DATA_DIR` | `= VIBEPROD_DATA_DIR` | тот же каталог в системе координат докер-демона. Нужен, когда брокер сам в контейнере |
| `VIBEPROD_OPENCODE_IMAGE` | `vibeprod-opencode:latest` | образ воркера, собирается из `worker/` при первом запуске |
| `VIBEPROD_WORKER_BUILD_DIR` | `./worker` | контекст сборки образа воркера |
| `VIBEPROD_IDLE_TTL_MIN` | `120` | минут простоя до убийства воркера |
| `VIBEPROD_PORT` | `8000` | порт брокера |
| `VIBEPROD_TZ` | `Europe/Moscow` | таймзона cron-расписаний |
| `VIBEPROD_GUARDIAN_URL` | `http://host.docker.internal:<порт>/guardian/mcp` | URL guardian MCP так, как его видят воркеры |
| `VIBEPROD_LOGIN` · `VIBEPROD_PASSWORD` | — | логин и пароль для входа в веб-интерфейс. Оба пусты — вход не требуется |
| `TELEGRAM_BOT_TOKEN` · `TELEGRAM_ALLOWED_USERS` · `TELEGRAM_WEB_URL` | — | фолбэк на первый запуск, если в интерфейсе ничего не настроено |
| `*_API_KEY` | — | прокидываются внутрь воркеров |

Аннотированная версия — в [.env.example](.env.example).

## API

```
GET/POST   /api/projects              PUT/DELETE /api/projects/{id}
GET/POST   /api/agents                PUT/DELETE /api/agents/{id}
POST/PUT/DELETE /api/agents/{id}/mcp[/{mid}]     PUT /api/agents/{id}/skills
GET/POST   /api/skills                PUT/DELETE /api/skills/{id}
GET/POST   /api/providers             PUT/DELETE /api/providers/{id}
POST       /api/providers/{id}/check          probe-контейнер: регистрация + модели + тест-запрос
GET/POST   /api/mcp-catalog           PUT/DELETE /api/mcp-catalog/{id}
POST       /api/mcp-catalog/{id}/start|stop   docker-сервисы
POST       /api/mcp-catalog/{id}/attach       {agent_id}
GET/POST   /api/webhooks              PUT/DELETE /api/webhooks/{id}
POST       /api/webhooks/{slug}/run           тело {prompt?, title?}, X-Webhook-Secret, ?wait=<сек>
GET        /api/channels
GET/PUT/DELETE /api/telegram          POST /api/telegram/test {token}
GET/POST   /api/sessions              POST /api/sessions/{id}/prompt|abort|restart
GET        /api/sessions/{id}/messages        DELETE /api/sessions/{id}
WS         /ws/sessions/{id}
GET/POST   /api/schedules             PUT/DELETE /api/schedules/{id}
POST       /api/schedules/{id}/run-now        GET /api/schedules/{id}/runs
POST       /guardian/mcp                      JSON-RPC поверх streamable HTTP, только с Bearer-секретом
```

Большинство коллекций принимает `?project_id=` для фильтрации.

## Устранение неполадок

**WebSocket не подключается, локальные запросы отдают 503.**
Брокер ходит в воркеры по `127.0.0.1`. Если включён системный HTTP-прокси
(macOS: `scutil --proxy`), python-клиенты и браузер маршрутизируют loopback через
него. Добавьте `127.0.0.1,localhost` в `no_proxy` и в исключения системного
прокси.

**Ошибка `Model not found: provider/model`.**
opencode регистрирует модели провайдера только при наличии API-ключа. Проверьте,
что ключ доехал до воркера, — кнопка «Проверить» на странице «Провайдеры»
проходит ровно этот путь и показывает полученный список моделей.

## Участие в разработке

См. [CONTRIBUTING.md](CONTRIBUTING.md). Тесты: `pytest -q` (докер и ключи не
нужны).

## Лицензия

MIT — см. [LICENSE](LICENSE). © 2026 Pavel Katskov.

Построено на [opencode](https://github.com/sst/opencode) от Anomaly Innovations,
тоже MIT.
