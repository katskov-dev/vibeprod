CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  description TEXT DEFAULT '',
  mode TEXT DEFAULT 'primary',
  model TEXT DEFAULT 'deepseek/deepseek-chat',
  temperature REAL,
  variant TEXT,
  system_prompt TEXT DEFAULT '',
  permission TEXT DEFAULT '"allow"',
  is_default INTEGER DEFAULT 0,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_mcp (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'local',
  command TEXT,
  url TEXT,
  headers TEXT,
  environment TEXT,
  enabled INTEGER DEFAULT 1,
  UNIQUE (agent_id, name)
);

CREATE TABLE IF NOT EXISTS skills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  description TEXT DEFAULT '',
  body TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_skills (
  agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  PRIMARY KEY (agent_id, skill_id)
);

CREATE TABLE IF NOT EXISTS providers (
  id TEXT PRIMARY KEY,
  label TEXT DEFAULT '',
  env_var TEXT NOT NULL,
  api_key TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  models TEXT,
  models_full TEXT,
  last_check_ok INTEGER,
  last_check_at TEXT,
  last_check_error TEXT,
  last_gen TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  agent_id INTEGER REFERENCES agents(id) ON DELETE SET NULL,
  agent_name TEXT,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  title TEXT,
  source TEXT DEFAULT 'manual',
  prompt TEXT,
  status TEXT DEFAULT 'queued',
  container_id TEXT,
  opencode_session_id TEXT,
  host_port INTEGER,
  auth_token TEXT,
  workspace TEXT,
  model TEXT,
  error TEXT,
  result_json TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  started_at TEXT,
  finished_at TEXT,
  last_activity TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  ts TEXT DEFAULT (datetime('now')),
  type TEXT,
  payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);

CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  title TEXT,
  prompt TEXT NOT NULL,
  cron TEXT NOT NULL,
  timezone TEXT DEFAULT 'Europe/Moscow',
  enabled INTEGER DEFAULT 1,
  last_run TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS schedule_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
  session_id TEXT,
  status TEXT DEFAULT 'queued',
  error TEXT,
  started_at TEXT,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS mcp_catalog (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  description TEXT DEFAULT '',
  kind TEXT DEFAULT 'generic',
  type TEXT DEFAULT 'remote',
  command TEXT,
  url TEXT,
  headers TEXT,
  environment TEXT,
  service_build_dir TEXT,
  service_container TEXT,
  service_port INTEGER,
  service_network TEXT DEFAULT 'harness-mcp',
  builtin INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS webhooks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT UNIQUE NOT NULL,
  agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT DEFAULT '',
  prompt TEXT DEFAULT '',
  secret TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1,
  last_run TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS telegram_chats (
  chat_id INTEGER PRIMARY KEY,
  session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
  agent_id INTEGER REFERENCES agents(id) ON DELETE SET NULL,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  message_id INTEGER,
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS telegram_config (
  project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  token TEXT DEFAULT '',
  allowed_users TEXT DEFAULT '',
  web_url TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1,
  bot_username TEXT,
  connected INTEGER DEFAULT 0,
  last_error TEXT,
  updated_at TEXT DEFAULT (datetime('now'))
);
