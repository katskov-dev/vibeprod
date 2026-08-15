// files MCP: файлы проекта в MinIO.
// Минимальный streamable-HTTP JSON-RPC (как guardian MCP у брокера),
// живёт в контейнере playwright, поэтому видит его скриншоты (/vibeprod-shots).
import http from "node:http";
import fs from "node:fs/promises";
import path from "node:path";
import { Client as S3Client } from "minio";

const PORT = parseInt(process.env.FILES_MCP_PORT || "8932", 10);
const SHOTS_DIR = process.env.VIBEPROD_SHOTS_DIR || "/vibeprod-shots";
const S3_ENDPOINT = process.env.VIBEPROD_S3_ENDPOINT || "http://vibeprod-minio:9000";
const S3_ACCESS = process.env.VIBEPROD_S3_ACCESS_KEY || "minioadmin";
const S3_SECRET = process.env.VIBEPROD_S3_SECRET_KEY || "minioadmin";
const FALLBACK_BROKER_URL = process.env.VIBEPROD_BROKER_URL || "http://host.docker.internal:8000";

const endPoint = S3_ENDPOINT.replace(/^https?:\/\//, "");
const [host, portStr] = endPoint.split(":");
const s3 = new S3Client({
  endPoint: host,
  port: portStr ? parseInt(portStr, 10) : S3_ENDPOINT.startsWith("https://") ? 443 : 9000,
  useSSL: S3_ENDPOINT.startsWith("https://"),
  accessKey: S3_ACCESS,
  secretKey: S3_SECRET,
});

const CONTENT_TYPES = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".svg": "image/svg+xml",
  ".avif": "image/avif",
  ".pdf": "application/pdf",
  ".txt": "text/plain",
  ".md": "text/markdown",
  ".html": "text/html",
  ".htm": "text/html",
  ".json": "application/json",
  ".csv": "text/csv",
  ".mp4": "video/mp4",
  ".webm": "video/webm",
  ".zip": "application/zip",
  ".gz": "application/gzip",
};

const TOOLS = [
  {
    name: "upload_file",
    description:
      "Загрузить локальный файл воркера в файлы проекта (MinIO) и получить публичную ссылку. " +
      "source — абсолютный путь в контейнере или имя файла из папки скриншотов playwright " +
      "(например, скриншот browser_take_screenshot с filename=\"/vibeprod-shots/отчёт.png\" " +
      "загружается как source=\"/vibeprod-shots/отчёт.png\"). " +
      "target — путь файла в файлах проекта, например \"shots/отчёт.png\". " +
      "Возвращает ссылку, которую можно вставить в ответ как markdown-картинку.",
    inputSchema: {
      type: "object",
      properties: {
        source: { type: "string", description: "Абсолютный путь к файлу или имя файла в /vibeprod-shots" },
        target: { type: "string", description: "Путь в файлах проекта (например shots/x.png)" },
        contentType: { type: "string", description: "MIME-тип (по умолчанию — по расширению)" },
      },
      required: ["source", "target"],
    },
  },
  {
    name: "list_files",
    description:
      "Список файлов проекта (MinIO). prefix — подпапка, например \"shots\".",
    inputSchema: {
      type: "object",
      properties: {
        prefix: { type: "string", description: "Подпапка для фильтра (необязательно)" },
      },
      required: [],
    },
  },
];

function ctxOf(req) {
  const h = req.headers || {};
  const project = (h["x-vibeprod-project"] || "").toString().trim();
  const token = (h["x-vibeprod-token"] || "").toString().trim();
  const broker = (h["x-broker-url"] || FALLBACK_BROKER_URL).toString().trim().replace(/\/+$/, "");
  return { project, token, broker };
}

function bucketFor(ctx) {
  if (!/^\d+$/.test(ctx.project)) {
    throw new Error(
      "Не определён проект воркера (заголовок X-Vibeprod-Project). " +
        "Подключите MCP «files» из каталога к агенту и перезапустите сессию."
    );
  }
  return `vibeprod-p${ctx.project}`;
}

function resolveSource(source) {
  if (path.isAbsolute(source)) return source;
  return path.join(SHOTS_DIR, source);
}

function guessType(name, explicit) {
  if (explicit) return explicit;
  const ext = path.extname(name || "").toLowerCase();
  return CONTENT_TYPES[ext] || "application/octet-stream";
}

function fileUrl(ctx, target) {
  const q = new URLSearchParams({
    project_id: ctx.project,
    path: target,
    token: ctx.token || "",
  });
  return `${ctx.broker}/api/files/content?${q.toString()}`;
}

async function ensureBucket(bucket) {
  const exists = await s3.bucketExists(bucket);
  if (!exists) await s3.makeBucket(bucket);
}

async function uploadFile(ctx, args) {
  const source = resolveSource(String(args.source || ""));
  const target = String(args.target || "").replace(/^\/+/, "");
  if (!target) throw new Error("target обязателен");
  const data = await fs.readFile(source);
  const bucket = bucketFor(ctx);
  await ensureBucket(bucket);
  await s3.putObject(bucket, target, data, data.length, {
    "Content-Type": guessType(target, args.contentType),
  });
  const url = fileUrl(ctx, target);
  return {
    ok: true,
    path: target,
    size: data.length,
    url,
    markdown: `![${target}](${url})`,
  };
}

async function listFiles(ctx, args) {
  const bucket = bucketFor(ctx);
  await ensureBucket(bucket);
  const prefix = String(args.prefix || "");
  const out = [];
  const stream = s3.listObjects(bucket, prefix, true);
  for await (const obj of stream) {
    out.push({ name: obj.name, size: obj.size, lastModified: obj.lastModified?.toISOString?.() || null });
  }
  out.sort((a, b) => a.name.localeCompare(b.name));
  return out;
}

const CALL = { upload_file: uploadFile, list_files: listFiles };

function ok(id, result) {
  return { jsonrpc: "2.0", id, result };
}
function err(id, code, message) {
  return { jsonrpc: "2.0", id, error: { code, message } };
}
function toolResult(text, isError = false) {
  return { content: [{ type: "text", text }], isError };
}

async function dispatchOne(msg) {
  if (typeof msg !== "object" || msg === null) return err(null, -32700, "Parse error");
  const id = msg.id;
  const method = msg.method || "";
  if (method === "initialize") {
    const v = (msg.params || {}).protocolVersion || "2024-11-05";
    return ok(id, {
      protocolVersion: v,
      capabilities: { tools: {} },
      serverInfo: { name: "vibeprod-files", version: "1.0.0" },
    });
  }
  if (method === "notifications/initialized" || method === "notifications/cancelled") return null;
  if (method === "ping") return ok(id, {});
  if (method === "tools/list") return ok(id, { tools: TOOLS });
  if (method === "tools/call") {
    const params = msg.params || {};
    const fn = CALL[params.name];
    if (!fn) return ok(id, toolResult(`неизвестный инструмент: ${params.name}`, true));
    try {
      const ctx = ctxOf(msg.__req || {});
      console.log(`[files-mcp] ${params.name} project=${ctx.project || "-"} broker=${ctx.broker} token=${ctx.token ? "yes" : "no"}`);
      const result = await fn(ctx, params.arguments || {});
      const text = typeof result === "string" ? result : JSON.stringify(result, null, 2);
      return ok(id, toolResult(text));
    } catch (e) {
      return ok(id, toolResult(`${e.message || e}`, true));
    }
  }
  return err(id, -32601, `Method not found: ${method}`);
}

async function handle(req, res) {
  if (req.method === "GET") {
    res.writeHead(405, { Allow: "POST" });
    return res.end();
  }
  if (req.method === "DELETE") {
    res.writeHead(202);
    return res.end();
  }
  if (req.method !== "POST") {
    res.writeHead(405, { Allow: "POST" });
    return res.end();
  }
  let body = "";
  for await (const chunk of req) body += chunk;
  let payload;
  try {
    payload = JSON.parse(body || "{}");
  } catch {
    res.writeHead(400, { "Content-Type": "application/json" });
    return res.end(JSON.stringify(err(null, -32700, "Parse error")));
  }
  const decorate = (m) => (m && typeof m === "object" ? { ...m, __req: req } : m);
  try {
    let responses;
    if (Array.isArray(payload)) {
      responses = (await Promise.all(payload.map((m) => dispatchOne(decorate(m))))).filter((r) => r !== null);
    } else {
      responses = await dispatchOne(decorate(payload));
    }
    if (responses === null || (Array.isArray(responses) && responses.length === 0)) {
      res.writeHead(202);
      return res.end();
    }
    res.writeHead(200, { "Content-Type": "application/json" });
    return res.end(JSON.stringify(responses));
  } catch (e) {
    res.writeHead(500, { "Content-Type": "application/json" });
    return res.end(JSON.stringify(err(null, -32603, `Internal error: ${e.message || e}`)));
  }
}

const server = http.createServer(handle);
server.listen(PORT, "0.0.0.0", () => {
  console.log(`[files-mcp] listening on :${PORT}, shots dir ${SHOTS_DIR}, s3 ${S3_ENDPOINT}`);
});
