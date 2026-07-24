/**
 * Облачный store-and-forward буфер сигналов TradingView (Cloudflare Worker + D1).
 *
 * Зачем: локальный бот видит алерт, только пока он запущен и TradingView жив.
 * Этот приёмник принимает webhook от TradingView СЕРВЕРНО (даже когда домашний ПК
 * выключен), хранит сигналы ≥7 дней, а бот ОПРАШИВАЕТ его (pull) — никаких входящих
 * портов на ПК. См. cloud/README.md.
 *
 * Роуты (токен — в пути URL, т.к. webhook TradingView умеет только фиксированный
 * URL + тело, без кастомных заголовков):
 *   POST /hook/<token>            — принять тело алерта, ответить 200 мгновенно.
 *   GET  /pull/<token>?after&limit — забрать сигналы с id > after (по возрастанию).
 *   GET  /head/<token>            — {max_id} для инициализации курсора бота.
 *
 * Дедуп ретраев TradingView — по хэшу (тело + минута). Ретенция 7 дней —
 * ежедневный cron + оппортунистически при вставке.
 *
 * Секрет HOOK_TOKEN — через `wrangler secret put HOOK_TOKEN` (НЕ в wrangler.toml).
 */

const RETENTION_SEC = 7 * 24 * 3600;   // хранить 7 дней
const DEDUP_WINDOW_SEC = 120;          // «тело+минута»: гасим ретраи в пределах 2 мин
const MAX_BODY_BYTES = 8192;           // алерты короткие; защита от мусора
const PULL_LIMIT_MAX = 500;

const enc = new TextEncoder();

function safeEqual(a, b) {
  const ea = enc.encode(a || ""), eb = enc.encode(b || "");
  if (ea.length !== eb.length) return false;
  let diff = 0;
  for (let i = 0; i < ea.length; i++) diff |= ea[i] ^ eb[i];
  return diff === 0;
}

async function sha256hex(str) {
  const buf = await crypto.subtle.digest("SHA-256", enc.encode(str));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });

const notFound = () => new Response("not found", { status: 404 });

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean); // [route, token]
    const route = parts[0];
    const token = parts[1];

    // Неизвестный роут или неверный токен → 404 (не раскрываем endpoint).
    if (!route || !token || !safeEqual(token, env.HOOK_TOKEN)) return notFound();

    const nowSec = Math.floor(Date.now() / 1000);

    // ── POST /hook/<token> — принять сигнал ────────────────────────────────
    if (route === "hook" && request.method === "POST") {
      let body = (await request.text()) || "";
      body = body.trim();
      if (!body) return json({ ok: true, stored: false, reason: "empty" });
      if (enc.encode(body).length > MAX_BODY_BYTES)
        return json({ ok: false, error: "body too large" }, 413);

      // Если тело — JSON {"message": "..."} , сохраняем именно message (как шлёт
      // локальный http-приёмник); иначе — тело как есть (обычный текст алерта).
      if (body[0] === "{") {
        try {
          const obj = JSON.parse(body);
          if (obj && typeof obj.message === "string" && obj.message.trim())
            body = obj.message.trim();
        } catch (_) { /* не JSON — оставляем как есть */ }
      }

      const hash = await sha256hex(body + "|" + Math.floor(nowSec / 60));
      const dup = await env.DB
        .prepare("SELECT 1 FROM signals WHERE hash = ? AND ts > ? LIMIT 1")
        .bind(hash, nowSec - DEDUP_WINDOW_SEC)
        .first();
      if (dup) return json({ ok: true, stored: false, reason: "duplicate" });

      const res = await env.DB
        .prepare("INSERT INTO signals (ts, body, hash) VALUES (?, ?, ?)")
        .bind(nowSec, body, hash)
        .run();

      // Оппортунистическая ретенция — в фоне, не задерживая ответ.
      ctx.waitUntil(
        env.DB.prepare("DELETE FROM signals WHERE ts < ?")
          .bind(nowSec - RETENTION_SEC).run().catch(() => {})
      );
      return json({ ok: true, stored: true, id: res.meta?.last_row_id ?? null });
    }

    // ── GET /pull/<token>?after=&limit= — забрать непрочитанные ─────────────
    if (route === "pull" && request.method === "GET") {
      const after = Math.max(0, parseInt(url.searchParams.get("after") || "0", 10) || 0);
      let limit = parseInt(url.searchParams.get("limit") || "100", 10) || 100;
      limit = Math.min(Math.max(1, limit), PULL_LIMIT_MAX);
      const { results } = await env.DB
        .prepare("SELECT id, ts, body, hash FROM signals WHERE id > ? ORDER BY id ASC LIMIT ?")
        .bind(after, limit)
        .all();
      return json(results || []);
    }

    // ── GET /head/<token> — максимальный id (инициализация курсора) ─────────
    if (route === "head" && request.method === "GET") {
      const row = await env.DB.prepare("SELECT MAX(id) AS max_id FROM signals").first();
      return json({ max_id: row?.max_id ?? 0 });
    }

    return notFound();
  },

  // Ежедневная очистка (cron в wrangler.toml). Ретенция гарантирована даже без
  // трафика POST.
  async scheduled(event, env, ctx) {
    const nowSec = Math.floor(Date.now() / 1000);
    ctx.waitUntil(
      env.DB.prepare("DELETE FROM signals WHERE ts < ?")
        .bind(nowSec - RETENTION_SEC).run()
    );
  },
};
