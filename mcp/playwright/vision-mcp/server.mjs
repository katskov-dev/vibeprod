// vision MCP: анализ изображений моделью DeepSeek vision.
// Живёт в контейнере playwright, поэтому видит его скриншоты (/vibeprod-shots).
// API-ключ тянет с брокера (провайдер «deepseek» или DEEPSEEK_API_KEY в env).
import http from "node:http";
import fs from "node:fs/promises";
import path from "node:path";

const PORT = parseInt(process.env.VISION_MCP_PORT || "8934", 10);
const SHOTS_DIR = process.env.VIBEPROD_SHOTS_DIR || "/vibeprod-shots";
const FALLBACK_BROKER_URL = process.env.VIBEPROD_BROKER_URL || "http://host.docker.internal:8000";
const API_BASE = (process.env.VIBEPROD_VISION_BASE_URL || "https://api.deepseek.com").replace(/\/+$/, "");
const MODEL = process.env.VIBEPROD_VISION_MODEL || "deepseek-v4-flash-vision-exp";
const MAX_FILE_BYTES = 16 * 1024 * 1024; // лимит base64-картинок по доке 32 MiB; берём с запасом вниз

const MIME_BY_EXT = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
};

class ToolError extends Error {}

// ---------- конфиг с брокера ----------

const configCache = { at: 0, value: null };
const CACHE_TTL_MS = 30_000;

function ctxOf(req) {
  const h = req.headers || {};
  const project = (h["x-vibeprod-project"] || "").toString().trim();
  const token = (h["x-vibeprod-token"] || "").toString().trim();
  const broker = (h["x-broker-url"] || FALLBACK_BROKER_URL).toString().trim().replace(/\/+$/, "");
  if (!/^\d+$/.test(project)) {
    throw new ToolError(
      "Не определён проект воркера (заголовок X-Vibeprod-Project). " +
        "Подключите MCP «vision» из каталога к агенту и перезапустите сессию."
    );
  }
  return { project, token, broker };
}

async function fetchConfig(ctx) {
  if (configCache.value && Date.now() - configCache.at < CACHE_TTL_MS) {
    return configCache.value;
  }
  const url = `${ctx.broker}/api/vision/config?project_id=${ctx.project}`;
  const resp = await fetch(url, { headers: { "X-Vibeprod-Token": ctx.token }, signal: AbortSignal.timeout(10_000) });
  if (!resp.ok) {
    throw new ToolError(`брокер не отдал конфиг vision (HTTP ${resp.status})`);
  }
  const cfg = await resp.json();
  configCache.at = Date.now();
  configCache.value = cfg;
  return cfg;
}

async function requireKey(ctx) {
  const cfg = await fetchConfig(ctx);
  if (!cfg.configured) {
    throw new ToolError(
      "DeepSeek vision не настроен: нет API-ключа. " +
        (cfg.hint || "") +
        " Настройте ключ, затем повторите вызов (конфиг кэшируется до 30 секунд)."
    );
  }
  return cfg;
}

// ---------- работа с изображениями ----------

function resolveImage(ref) {
  const s = String(ref || "").trim();
  if (!s) throw new ToolError("image: укажите путь к файлу, URL или data-URL");
  return s;
}

async function toContentBlock(ref) {
  const s = resolveImage(ref);
  if (/^data:image\/[a-z0-9.+-]+;base64,/i.test(s)) {
    return { type: "image_url", image_url: { url: s } };
  }
  if (/^https?:\/\//i.test(s)) {
    if (s.length > 8192) {
      throw new ToolError("URL изображения длиннее 8192 символов — передайте файл путём или data-URL");
    }
    return { type: "image_url", image_url: { url: s } };
  }
  const file = path.isAbsolute(s) ? s : path.join(SHOTS_DIR, s);
  let data;
  try {
    data = await fs.readFile(file);
  } catch {
    throw new ToolError(`файл не найден: ${file} (для скриншотов playwright укажите путь в ${SHOTS_DIR})`);
  }
  if (data.length === 0) throw new ToolError(`файл пуст: ${file}`);
  if (data.length > MAX_FILE_BYTES) {
    throw new ToolError(`файл больше 16 MiB (${Math.round(data.length / 1024 / 1024)} MiB) — уменьшите изображение`);
  }
  const mime = MIME_BY_EXT[path.extname(file).toLowerCase()] || "image/png";
  return {
    type: "image_url",
    image_url: { url: `data:${mime};base64,${data.toString("base64")}` },
  };
}

// ---------- вызов DeepSeek ----------

async function callVision(apiKey, baseUrl, model, blocks) {
  const body = {
    model,
    messages: [
      { role: "user", content: blocks },
    ],
  };
  const resp = await fetch(`${baseUrl}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(120_000),
  });
  const raw = await resp.text();
  let parsed = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    parsed = null;
  }
  if (!resp.ok) {
    const msg =
      (parsed && (parsed.error?.message || parsed.message)) ||
      (parsed && typeof parsed.error === "string" ? parsed.error : "") ||
      raw.slice(0, 300) ||
      resp.statusText;
    if (/does not support image|not support.*image/i.test(msg)) {
      throw new ToolError(
        `Модель ${model} не поддерживает изображения (HTTP ${resp.status}): ${msg}. ` +
          "Проверьте VIBEPROD_VISION_MODEL у брокера."
      );
    }
    if (resp.status === 401 || /invalid api key|authentication/i.test(msg)) {
      throw new ToolError(
        `DeepSeek отклонил API-ключ (HTTP ${resp.status}): ${msg}. Проверьте ключ на странице «Провайдеры» (кнопка «Проверить»).`
      );
    }
    throw new ToolError(`DeepSeek API (HTTP ${resp.status}): ${msg}`);
  }
  const answer = parsed?.choices?.[0]?.message?.content ?? "";
  return {
    answer,
    model: parsed?.model || model,
    usage: parsed?.usage || null,
  };
}

// ---------- инструменты ----------

async function h_vision_status(args, ctx) {
  const cfg = await fetchConfig(ctx);
  if (!cfg.configured) {
    return {
      configured: false,
      model: cfg.model,
      message: cfg.hint || "DeepSeek vision не настроен.",
    };
  }
  return {
    configured: true,
    model: cfg.model,
    base_url: cfg.base_url,
    api_key_source: cfg.source,
    api_key_masked: cfg.api_key_masked,
  };
}

async function h_vision_analyze(args, ctx) {
  const cfg = await requireKey(ctx);
  const prompt = String(args.prompt || "").trim();
  if (!prompt) throw new ToolError("prompt обязателен: что спросить про изображение");
  const refs = [];
  if (args.image) refs.push(args.image);
  if (Array.isArray(args.images)) refs.push(...args.images);
  if (!refs.length) {
    throw new ToolError(
      "Не передано изображение. image — путь к файлу (например, скриншот playwright " +
        "в /vibeprod-shots/...), http(s)-URL или data-URL."
    );
  }
  if (refs.length > 20) throw new ToolError("максимум 20 изображений за вызов");
  const detail = args.detail ? String(args.detail) : "";
  if (detail && !["low", "original", "auto"].includes(detail)) {
    throw new ToolError("detail: low | original | auto");
  }
  const blocks = [{ type: "text", text: prompt }];
  for (const ref of refs) {
    blocks.push(await toContentBlock(ref));
  }
  const imageBlocks = blocks.filter((b) => b.type === "image_url");
  for (const b of imageBlocks) {
    if (detail) b.image_url.detail = detail;
  }
  const result = await callVision(cfg.api_key, cfg.base_url, cfg.model, blocks);
  return result;
}

const CALL = {
  vision_status: h_vision_status,
  vision_analyze: h_vision_analyze,
};

const TOOLS = [
  {
    name: "vision_status",
    description:
      "Статус DeepSeek vision: настроен ли API-ключ (провайдер «deepseek» на странице «Провайдеры» " +
      "или DEEPSEEK_API_KEY в env брокера), какая модель и источник ключа. Вызывай, если " +
      "vision_analyze вернул ошибку настройки.",
    inputSchema: { type: "object", properties: {}, required: [] },
  },
  {
    name: "vision_analyze",
    description:
      "Проанализировать изображение моделью DeepSeek vision (deepseek-v4-flash-vision-exp): " +
      "описать картинку, прочитать текст со скриншота, проверить вёрстку. " +
      "image — путь к файлу в контейнере (скриншоты playwright: путь в /vibeprod-shots, " +
      "например «/vibeprod-shots/отчёт.png»), http(s)-URL или data-URL; " +
      "images — массив до 20 изображений; prompt — вопрос/задание по изображению; " +
      "detail — «low» (быстрее/дешевле), «original» или «auto». Возвращает ответ модели.",
    inputSchema: {
      type: "object",
      properties: {
        image: { type: "string", description: "Изображение: путь (/vibeprod-shots/...), URL или data-URL" },
        images: { type: "array", items: { type: "string" }, description: "Несколько изображений (до 20)" },
        prompt: { type: "string", description: "Вопрос или задание по изображению" },
        detail: { type: "string", enum: ["low", "original", "auto"], description: "Детализация обработки" },
      },
      required: ["prompt"],
    },
  },
];

// ---------- streamable HTTP JSON-RPC ----------

function ok(msgId, result) {
  return { jsonrpc: "2.0", id: msgId, result };
}

function err(msgId, code, message) {
  return { jsonrpc: "2.0", id: msgId, error: { code, message } };
}

function toolResult(text, isError = false) {
  return { content: [{ type: "text", text }], isError };
}

async function dispatchOne(msg, req) {
  if (typeof msg !== "object" || msg === null) return err(null, -32700, "Parse error");
  const msgId = msg.id;
  const method = msg.method || "";
  if (method === "initialize") {
    const version = (msg.params || {}).protocolVersion || "2024-11-05";
    return ok(msgId, {
      protocolVersion: version,
      capabilities: { tools: {} },
      serverInfo: { name: "vibeprod-vision", version: "1.0.0" },
    });
  }
  if (method === "notifications/initialized" || method === "notifications/cancelled") return null;
  if (method === "ping") return ok(msgId, {});
  if (method === "tools/list") return ok(msgId, { tools: TOOLS });
  if (method === "tools/call") {
    const params = msg.params || {};
    const fn = CALL[params.name];
    if (!fn) return ok(msgId, toolResult(`неизвестный инструмент: ${params.name}`, true));
    try {
      const ctx = ctxOf(req);
      const result = await fn(params.arguments || {}, ctx);
      const text = typeof result === "string" ? result : JSON.stringify(result, null, 2);
      return ok(msgId, toolResult(text));
    } catch (e) {
      if (e instanceof ToolError) return ok(msgId, toolResult(e.message, true));
      console.error("vision tool error:", e);
      return ok(msgId, toolResult(`${e?.name || "Error"}: ${e?.message || e}`, true));
    }
  }
  return err(msgId, -32601, `Method not found: ${method}`);
}

async function dispatch(payload, req) {
  if (Array.isArray(payload)) {
    const responses = await Promise.all(payload.map((m) => dispatchOne(m, req)));
    return responses.filter((r) => r !== null);
  }
  return dispatchOne(payload, req);
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks);
}

function send(resp, status, body) {
  const data = Buffer.from(typeof body === "string" ? body : "", "utf8");
  resp.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": data.length,
  });
  resp.end(data);
}

const server = http.createServer(async (req, resp) => {
  if (req.method === "DELETE") {
    resp.writeHead(202, { "Content-Length": 0 });
    resp.end();
    return;
  }
  if (req.method !== "POST") {
    send(resp, 405, JSON.stringify(err(null, -32000, "only POST")));
    return;
  }
  let payload;
  try {
    payload = JSON.parse((await readBody(req)).toString("utf8") || "{}");
  } catch {
    send(resp, 400, JSON.stringify(err(null, -32700, "Parse error")));
    return;
  }
  try {
    const responses = await dispatch(payload, req);
    if (responses === null || (Array.isArray(responses) && responses.length === 0)) {
      resp.writeHead(202, { "Content-Length": 0 });
      resp.end();
      return;
    }
    send(resp, 200, JSON.stringify(responses));
  } catch (e) {
    console.error("dispatch:", e);
    send(resp, 500, JSON.stringify(err(null, -32603, `Internal error: ${e?.message || e}`)));
  }
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`vibeprod-vision-mcp listening on :${PORT}`);
});
