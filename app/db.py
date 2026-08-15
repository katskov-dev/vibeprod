import os
import secrets
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("VIBEPROD_DATA_DIR", Path(__file__).resolve().parent.parent / "data")) / "vibeprod.db"


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
                    f"ALTER TABLE {table} ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL"
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
        prcols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
        if "file_token" not in prcols:
            conn.execute("ALTER TABLE projects ADD COLUMN file_token TEXT")
        for row in conn.execute("SELECT id FROM projects WHERE file_token IS NULL OR file_token=''"):
            conn.execute("UPDATE projects SET file_token=? WHERE id=?", (secrets.token_urlsafe(24), row["id"]))
        gcols = {r["name"] for r in conn.execute("PRAGMA table_info(agents)").fetchall()}
        if "is_guardian" not in gcols:
            conn.execute("ALTER TABLE agents ADD COLUMN is_guardian INTEGER DEFAULT 0")
        tgcols = {r["name"] for r in conn.execute("PRAGMA table_info(telegram_chats)").fetchall()}
        if "project_id" not in tgcols:
            conn.execute(
                "ALTER TABLE telegram_chats ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL"
            )
        tccols = {r["name"] for r in conn.execute("PRAGMA table_info(telegram_config)").fetchall()}
        if "notify_chat_id" not in tccols:
            conn.execute("ALTER TABLE telegram_config ADD COLUMN notify_chat_id TEXT DEFAULT ''")
        if "notify_mode" not in tccols:
            conn.execute("ALTER TABLE telegram_config ADD COLUMN notify_mode TEXT DEFAULT 'all'")
        if not conn.execute("SELECT id FROM mcp_catalog WHERE name='playwright'").fetchone():
            conn.execute(
                "INSERT INTO mcp_catalog(name, description, kind, type, url, "
                "service_build_dir, service_container, service_port, service_network, builtin) "
                "VALUES('playwright', 'Браузер Playwright в отдельном контейнере: навигация, "
                "скриншоты, клики, заполнение форм', 'service', 'remote', "
                "'http://vibeprod-playwright:8931/mcp', 'mcp/playwright', "
                "'vibeprod-playwright', 8931, 'vibeprod-mcp', 1)"
            )
        if not conn.execute("SELECT id FROM mcp_catalog WHERE name='files'").fetchone():
            conn.execute(
                "INSERT INTO mcp_catalog(name, description, kind, type, url, headers, "
                "service_build_dir, service_container, service_port, service_network, builtin) "
                "VALUES('files', 'Файлы проекта: загрузка локальных файлов воркера "
                "(например, скриншотов playwright) в хранилище MinIO проекта и публичные "
                "ссылки на них. Живёт в контейнере playwright.', 'service', 'remote', "
                "'http://vibeprod-playwright:8932/mcp', "
                "'{\"X-Vibeprod-Project\": \"{env:VIBEPROD_PROJECT_ID}\", "
                "\"X-Vibeprod-Token\": \"{env:VIBEPROD_FILE_TOKEN}\", "
                "\"X-Broker-Url\": \"{env:VIBEPROD_BROKER_URL}\"}', "
                "'mcp/playwright', 'vibeprod-playwright', 8932, 'vibeprod-mcp', 1)"
            )
        if not conn.execute("SELECT id FROM skills WHERE name='screenshot-to-files'").fetchone():
            conn.execute(
                "INSERT INTO skills(name, description, body) VALUES('screenshot-to-files', "
                "'Скриншоты страниц в файлы проекта (playwright + files MCP)', ?)",
                (
                    "Скриншот страницы с сохранением в файлы проекта.\n\n"
                    "1. Сделай скриншот через инструмент playwright MCP `browser_take_screenshot` "
                    "с filename внутри `/vibeprod-shots/`, например `/vibeprod-shots/отчёт.png` "
                    "(если указать просто имя без пути, файл окажется в `/vibeprod-shots`).\n"
                    "2. Загрузи файл в файлы проекта инструментом files MCP `upload_file`: "
                    "source — путь из шага 1, target — путь в файлах проекта, например "
                    "`shots/отчёт.png`.\n"
                    "3. Вставь полученную ссылку в ответ как markdown-картинку "
                    "`![подпись](<ссылка>)` — она отобразится в интерфейсе.\n\n"
                    "Если нужно несколько скриншотов — повтори шаги для каждого, "
                    "используй понятные имена файлов.",
                ),
            )
        if not conn.execute("SELECT id FROM projects LIMIT 1").fetchone():
            conn.execute(
                "INSERT INTO projects(name, description, file_token) VALUES('Основной', 'Проект по умолчанию', ?)",
                (secrets.token_urlsafe(24),),
            )
        for row in conn.execute("SELECT id FROM projects WHERE file_token IS NULL OR file_token=''"):
            conn.execute("UPDATE projects SET file_token=? WHERE id=?", (secrets.token_urlsafe(24), row["id"]))
        pid = conn.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()[0]
        for table in ("agents", "sessions", "schedules", "providers"):
            conn.execute(f"UPDATE {table} SET project_id=? WHERE project_id IS NULL", (pid,))
        for key in ("guardian_secret", "auth_secret"):
            if not conn.execute("SELECT key FROM settings WHERE key=?", (key,)).fetchone():
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?)",
                    (key, secrets.token_urlsafe(32)),
                )
        if not conn.execute("SELECT id FROM agents WHERE is_guardian=1 LIMIT 1").fetchone():
            from .guardian_prompt import GUARDIAN_SYSTEM_PROMPT

            model = os.environ.get("VIBEPROD_DEFAULT_MODEL") or "deepseek/deepseek-chat"
            default_agent = conn.execute(
                "SELECT model FROM agents WHERE is_guardian=0 ORDER BY is_default DESC, id LIMIT 1"
            ).fetchone()
            if default_agent and default_agent[0]:
                model = default_agent[0]
            conn.execute(
                "INSERT INTO agents(name, description, mode, model, system_prompt, permission, is_default, is_guardian, project_id) "
                "VALUES('vibeprod', 'Системный агент-оператор: настраивает проект через guardian MCP', 'primary', "
                "?, ?, '\"allow\"', 0, 1, ?)",
                (model, GUARDIAN_SYSTEM_PROMPT, pid),
            )
        conn.execute(
            "UPDATE agents SET name='vibeprod' WHERE is_guardian=1 AND name='guardian' "
            "AND NOT EXISTS (SELECT 1 FROM agents WHERE is_guardian=0 AND name='vibeprod')"
        )
        from .guardian_prompt import GUARDIAN_SYSTEM_PROMPT

        conn.execute(
            "UPDATE agents SET system_prompt=? WHERE is_guardian=1", (GUARDIAN_SYSTEM_PROMPT,)
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
