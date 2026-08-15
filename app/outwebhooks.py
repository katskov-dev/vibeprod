"""Исходящие вебхуки: доставка событий брокера на внешние URL.

Подписки живут в таблице out_webhooks (URL + список событий + секрет для
HMAC-подписи). dispatch() вызывается шиной событий (events.py) из любого
потока: для каждой подходящей подписки создаётся строка в
out_webhook_deliveries и фоновая задача доставки с retry-бэкoффом — стример
и API не блокируются.

Доставка: POST тела {event, timestamp, data} с заголовками X-Vibeprod-Event,
X-Vibeprod-Delivery и (при заданном секрете) X-Vibeprod-Signature — HMAC-SHA256
от сырых байтов тела. Ретраи на сетевые ошибки, 429 и 5xx; 4xx — сразу failed.
"""
import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime

import httpx

from . import db

log = logging.getLogger("vibeprod.outwebhooks")

RETRY_DELAYS = [1, 5, 15, 60, 300]
MAX_DELIVERIES = 200
DELIVERY_TIMEOUT = 15.0
USER_AGENT = "Vibeprod/1.0"

_deliver_semaphore = asyncio.Semaphore(10)


def _matching_webhooks(event):
    rows = db.query("SELECT * FROM out_webhooks WHERE enabled=1")
    out = []
    for row in rows:
        try:
            if event in json.loads(row["events"] or "[]"):
                out.append(row)
        except ValueError:
            continue
    return out


def _timestamp():
    return datetime.utcnow().isoformat() + "Z"


def enqueue(row, event, data):
    """Создаёт строку доставки (без запуска). Возвращает id доставки."""
    body = {"event": event, "timestamp": _timestamp(), "data": data or {}}
    did = db.execute(
        "INSERT INTO out_webhook_deliveries(webhook_id, event, payload, status, started_at) "
        "VALUES(?,?,?,'pending',datetime('now'))",
        (row["id"], event, json.dumps(body, ensure_ascii=False)),
    )
    _trim_journal()
    return did


def _schedule(did, main_loop):
    if main_loop is None:
        from .main import MAIN_LOOP

        main_loop = MAIN_LOOP
    if main_loop is None:
        db.execute(
            "UPDATE out_webhook_deliveries SET status='failed', error='no event loop', finished_at=datetime('now') WHERE id=?",
            (did,),
        )
        log.error("no main loop, delivery %s dropped", did)
        return
    asyncio.run_coroutine_threadsafe(deliver(did), main_loop)


def dispatch(event, data=None, main_loop=None):
    """Разослать событие всем подходящим подпискам. Потокобезопасно."""
    for row in _matching_webhooks(event):
        _schedule(enqueue(row, event, data), main_loop)


def deliver_to(webhook_id, event, data=None, main_loop=None):
    """Доставка одной конкретной подписке (тест из UI и т.п.)."""
    row = db.query_one("SELECT * FROM out_webhooks WHERE id=?", (webhook_id,))
    if not row:
        return None
    did = enqueue(row, event, data)
    _schedule(did, main_loop)
    return did


async def deliver(did, attempts_limit=None):
    """Фоновая доставка с ретраями. attempts_limit — максимум попыток в этом запуске."""
    async with _deliver_semaphore:
        delivery = db.query_one(
            "SELECT d.*, w.url, w.secret FROM out_webhook_deliveries d "
            "JOIN out_webhooks w ON w.id=d.webhook_id WHERE d.id=?",
            (did,),
        )
        if not delivery or delivery["status"] in ("success", "failed"):
            return
        body = (delivery["payload"] or "").encode()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-Vibeprod-Event": delivery["event"],
            "X-Vibeprod-Delivery": str(did),
        }
        secret = (delivery["secret"] or "").strip()
        if secret:
            headers["X-Vibeprod-Signature"] = "sha256=" + hmac.new(
                secret.encode(), body, hashlib.sha256
            ).hexdigest()
        done = delivery["attempts"] or 0
        max_attempts = len(RETRY_DELAYS) if attempts_limit is None else min(attempts_limit, len(RETRY_DELAYS))
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(DELIVERY_TIMEOUT, connect=5.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for attempt in range(done + 1, max_attempts + 1):
                last = attempt == max_attempts
                try:
                    r = await client.post(delivery["url"], content=body, headers=headers)
                    code = r.status_code
                    err = None if 200 <= code < 300 else f"HTTP {code}"
                    retryable = code == 429 or code >= 500
                except asyncio.CancelledError:
                    raise
                except (httpx.HTTPError, OSError) as exc:
                    code = None
                    err = str(exc)[:500]
                    retryable = True
                if err is None:
                    _finish(did, delivery["webhook_id"], "success", code, attempt, None)
                    return
                if not retryable or last:
                    _finish(did, delivery["webhook_id"], "failed", code, attempt, err)
                    return
                db.execute(
                    "UPDATE out_webhook_deliveries SET status='retrying', attempts=?, http_status=?, error=? WHERE id=?",
                    (attempt, code, err, did),
                )
                await asyncio.sleep(RETRY_DELAYS[attempt - 1])
        if done >= max_attempts:
            _finish(did, delivery["webhook_id"], "failed", delivery["http_status"], done, delivery["error"] or "нет попыток")


def _finish(did, webhook_id, status, http_status, attempts, error):
    db.execute(
        "UPDATE out_webhook_deliveries SET status=?, http_status=?, attempts=?, error=?, finished_at=datetime('now') WHERE id=?",
        (status, http_status, attempts, error, did),
    )
    db.execute("UPDATE out_webhooks SET last_delivery_at=datetime('now') WHERE id=?", (webhook_id,))


def _trim_journal():
    """Держим не более MAX_DELIVERIES строк журнала на подписку."""
    for row in db.query(
        "SELECT webhook_id FROM out_webhook_deliveries GROUP BY webhook_id HAVING COUNT(*) > ?",
        (MAX_DELIVERIES,),
    ):
        db.execute(
            "DELETE FROM out_webhook_deliveries WHERE id IN "
            "(SELECT id FROM out_webhook_deliveries WHERE webhook_id=? ORDER BY id DESC LIMIT -1 OFFSET ?)",
            (row["webhook_id"], MAX_DELIVERIES),
        )


async def requeue_pending():
    """После рестарта брокера: перезапускаем недоставленное (best effort, один раз)."""
    rows = db.query(
        "SELECT d.id FROM out_webhook_deliveries d JOIN out_webhooks w ON w.id=d.webhook_id "
        "WHERE d.status IN ('pending','retrying') AND w.enabled=1"
    )
    for row in rows:
        asyncio.create_task(deliver(row["id"]))
    if rows:
        log.info("out-webhooks: requeued %d pending deliveries", len(rows))
