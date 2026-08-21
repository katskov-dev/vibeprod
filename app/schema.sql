CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  file_token TEXT,
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
  memory TEXT DEFAULT '',
  memory_enabled INTEGER DEFAULT 1,
  is_default INTEGER DEFAULT 0,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_calls (
  caller_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  target_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  PRIMARY KEY (caller_id, target_id)
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

CREATE TABLE IF NOT EXISTS session_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  msg_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_messages_session ON session_messages(session_id, seq);

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
  service_network TEXT DEFAULT 'vibeprod-mcp',
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

CREATE TABLE IF NOT EXISTS out_webhooks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT DEFAULT '',
  url TEXT NOT NULL,
  events TEXT NOT NULL DEFAULT '["session.completed","session.failed"]',
  secret TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1,
  last_delivery_at TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS out_webhook_deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  webhook_id INTEGER NOT NULL REFERENCES out_webhooks(id) ON DELETE CASCADE,
  event TEXT NOT NULL,
  payload TEXT,
  status TEXT DEFAULT 'pending',
  http_status INTEGER,
  attempts INTEGER DEFAULT 0,
  error TEXT,
  started_at TEXT,
  finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_out_deliveries_webhook ON out_webhook_deliveries(webhook_id);

CREATE TABLE IF NOT EXISTS telegram_config (
  project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  token TEXT DEFAULT '',
  allowed_users TEXT DEFAULT '',
  web_url TEXT DEFAULT '',
  notify_chat_id TEXT DEFAULT '',
  notify_mode TEXT DEFAULT 'all',
  enabled INTEGER DEFAULT 1,
  bot_username TEXT,
  connected INTEGER DEFAULT 0,
  last_error TEXT,
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  description TEXT DEFAULT '',
  status TEXT DEFAULT 'open',
  tags TEXT DEFAULT '',
  created_by TEXT DEFAULT 'manual',
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_issues_project ON issues(project_id);

CREATE TABLE IF NOT EXISTS ssh_servers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  host TEXT NOT NULL,
  port INTEGER DEFAULT 22,
  username TEXT NOT NULL,
  auth_type TEXT DEFAULT 'key',
  private_key TEXT DEFAULT '',
  password TEXT DEFAULT '',
  known_hosts TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1,
  last_error TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ssh_commands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  server_id INTEGER NOT NULL REFERENCES ssh_servers(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  command TEXT NOT NULL,
  arg_regex TEXT DEFAULT '',
  timeout INTEGER DEFAULT 60,
  enabled INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now')),
  UNIQUE(server_id, name)
);

CREATE TABLE IF NOT EXISTS ssh_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
  server_id INTEGER REFERENCES ssh_servers(id) ON DELETE SET NULL,
  command_id INTEGER REFERENCES ssh_commands(id) ON DELETE SET NULL,
  command_name TEXT DEFAULT '',
  params TEXT DEFAULT '',
  status TEXT DEFAULT 'ok',
  exit_code INTEGER,
  output TEXT DEFAULT '',
  duration_ms INTEGER,
  started_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ssh_runs_server ON ssh_runs(server_id, id DESC);
