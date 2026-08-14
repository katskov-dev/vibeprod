import os
import secrets
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("HARNESS_DATA_DIR", Path(__file__).resolve().parent.parent / "data")) / "harness.db"


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = connect()
    try:
        conn.executescript((Path(__file__).parent / "schema.sql").read_text())
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(providers)").fetchall()}
        if "last_gen" not in cols:
            conn.execute("ALTER TABLE providers ADD COLUMN last_gen TEXT")
        for table in ("agents", "sessions", "schedules", "providers"):
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "project_id" not in cols:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN project_id INTEGER "
                    f"REFERENCES projects(id) ON DELETE SET NULL"
                )
        scols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "source" not in scols:
            conn.execute("ALTER TABLE sessions ADD COLUMN source TEXT DEFAULT 'manual'")
        acols = {r["name"] for r in conn.execute("PRAGMA table_info(agents)").fetchall()}
        if "variant" not in acols:
            conn.execute("ALTER TABLE agents ADD COLUMN variant TEXT")
        pcols = {r["name"] for r in conn.execute("PRAGMA table_info(providers)").fetchall()}
        if "models_full" not in pcols:
            conn.execute("ALTER TABLE providers ADD COLUMN models_full TEXT")
        gcols = {r["name"] for r in conn.execute("PRAGMA table_info(agents)").fetchall()}
        if "is_guardian" not in gcols:
            conn.execute("ALTER TABLE agents ADD COLUMN is_guardian INTEGER DEFAULT 0")
        tgcols = {r["name"] for r in conn.execute("PRAGMA table_info(telegram_chats)").fetchall()}
        if "project_id" not in tgcols:
            conn.execute("ALTER TABLE telegram_chats ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
        if not conn.execute("SELECT id FROM mcp_catalog WHERE name='playwright'").fetchone():
            conn.execute(
                "INSERT INTO mcp_catalog(name, description, kind, type, url, "
                "service_build_dir, service_container, service_port, service_network, builtin) "
                "VALUES('playwright', 'Браузер Playwright в отдельном контейнере: навигация, "
                "скриншоты, клики, заполнение форм', 'service', 'remote', "
                "'http://harness-playwright:8931/mcp', 'mcp/playwright', "
                "'harness-playwright', 8931, 'harness-mcp', 1)"
            )
        if not conn.execute("SELECT id FROM projects LIMIT 1").fetchone():
            conn.execute(
                "INSERT INTO projects(name, description) VALUES('Основной', 'Проект по умолчанию')"
            )
        pid = conn.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()[0]
        for table in ("agents", "sessions", "schedules", "providers"):
            conn.execute(f"UPDATE {table} SET project_id=? WHERE project_id IS NULL", (pid,))
        if not conn.execute("SELECT key FROM settings WHERE key='guardian_secret'").fetchone():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES('guardian_secret', ?)",
                (secrets.token_urlsafe(32),),
            )
        if not conn.execute("SELECT id FROM agents WHERE is_guardian=1 LIMIT 1").fetchone():
            from .guardian_prompt import GUARDIAN_SYSTEM_PROMPT

            model = os.environ.get("HARNESS_DEFAULT_MODEL") or "deepseek/deepseek-chat"
            default_agent = conn.execute(
                "SELECT model FROM agents WHERE is_guardian=0 ORDER BY is_default DESC, id LIMIT 1"
            ).fetchone()
            if default_agent and default_agent[0]:
                model = default_agent[0]
            conn.execute(
                "INSERT INTO agents(name, description, mode, model, system_prompt, permission, is_default, is_guardian, project_id) "
                "VALUES('guardian', 'Системный агент-оператор: настраивает проект через guardian MCP', 'primary', "
                "?, ?, '\"allow\"', 0, 1, ?)",
                (model, GUARDIAN_SYSTEM_PROMPT, pid),
            )
        conn.commit()
    finally:
        conn.close()


def query(sql, params=()):
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    conn = connect()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def exec_many(sql, seq):
    conn = connect()
    try:
        conn.executemany(sql, seq)
        conn.commit()
    finally:
        conn.close()
