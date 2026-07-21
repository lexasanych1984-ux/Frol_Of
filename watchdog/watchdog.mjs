#!/usr/bin/env node
/**
 * TradingView config watchdog — СТРОГО READ-ONLY.
 *
 * Сверяет живое состояние 6 боевых strategy-алертов TradingView с эталоном
 * (watchdog/etalons.json) и шлёт в Telegram [WATCHDOG] при любом расхождении.
 * Раз в сутки на чистом прогоне — короткое «OK».
 *
 * Почему алерты, а не инстансы на графике: strategy-алерт хранит СВОЙ снимок
 * инпутов; именно он исполняется на сервере TV и кормит бота/пуши. Это ровно
 * то, что «слетало на дефолты». list_alerts отдаёт эти замороженные inputs —
 * читаем их одним запросом, НИЧЕГО не пишем в TV и НЕ трогаем лейауты.
 *
 * Режимы:
 *   node watchdog.mjs               — проверка (по умолчанию); шлёт Telegram
 *   node watchdog.mjs --dry         — проверка без Telegram (печать в консоль/лог)
 *   node watchdog.mjs --baseline    — снять живые алерты в etalons.json (эталон)
 *   node watchdog.mjs --test-telegram — отправить тестовое сообщение и выйти
 *   флаги: --force (игнор анти-спама), --json (машинный вывод результата)
 *
 * Коды выхода: 0 = OK, 1 = расхождение, 2 = TV недоступен / ошибка.
 */
import fs from 'node:fs';
import path from 'node:path';
import https from 'node:https';
import { fileURLToPath } from 'node:url';
import CDP from 'chrome-remote-interface';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ETALONS_PATH = path.join(HERE, 'etalons.json');
const LABELS_PATH = path.join(HERE, 'labels.json');
const STATE_PATH = path.join(HERE, 'state.json');
const LOG_DIR = path.join(HERE, 'logs');
const LOG_PATH = path.join(LOG_DIR, 'watchdog.log');

const CDP_HOST = process.env.TV_CDP_HOST || process.env.CDP_HOST || '127.0.0.1';
const CDP_PORT = Number(process.env.TV_CDP_PORT || process.env.CDP_PORT) || 9222;

const PREFIX = process.env.WATCHDOG_PREFIX || '[WATCHDOG]';
// Не повторять ИДЕНТИЧНОЕ уведомление чаще этого (мин). Два прогона в сутки
// (~12ч) всё равно оба проходят → расхождение напоминает дважды в день.
const ANTISPAM_MIN = Number(process.env.WATCHDOG_ANTISPAM_MIN) || 300;

// Путь к .env бота, из которого берём тот же Telegram-канал, что у heartbeat.
const BOT_ENV = process.env.WATCHDOG_BOT_ENV || 'C:\\Users\\lexas\\bybit-tradfi-bot\\.env';

/**
 * Манифест 6 боевых потоков: alert_id + читаемое имя + ожидаемая активность.
 * Инпуты/символ/ТФ/версия снимаются с живых алертов при --baseline.
 * expected_active берётся ОТСЮДА (не из живого состояния), поэтому выключенный
 * поток остаётся расхождением, пока его не включат.
 */
const STREAMS = [
  { alert_id: 5140264864, name: 'SMC EURUSD 15m', strategy: 'smc',    expected_active: true },
  { alert_id: 5140434449, name: 'SMC GBPJPY 15m', strategy: 'smc',    expected_active: true },
  { alert_id: 5140434481, name: 'SMC GBPJPY 1H',  strategy: 'smc',    expected_active: true },
  { alert_id: 5146158866, name: 'CRT GER40 1H',   strategy: 'crt',    expected_active: true },
  { alert_id: 5177587028, name: 'Asia GER40 5m',  strategy: 'asweep', expected_active: true },
  { alert_id: 5177587639, name: 'Asia NAS100 5m', strategy: 'asweep', expected_active: true },
];

const args = process.argv.slice(2);
const flag = (f) => args.includes(f);
const MODE_BASELINE = flag('--baseline');
const MODE_TEST_TG = flag('--test-telegram');
const DRY = flag('--dry');
const FORCE = flag('--force');
const JSON_OUT = flag('--json');

// ─────────────────────────── утилиты ───────────────────────────

function ts() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function localDate() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// Лог в UTF-8 с BOM в начале файла — чтобы Get-Content в Windows PowerShell 5.1
// без -Encoding читал кириллицу правильно (тот же приём, что в watchdog бота).
function log(msg) {
  const line = `${ts()} ${msg}`;
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    const fresh = !fs.existsSync(LOG_PATH) || fs.statSync(LOG_PATH).size === 0;
    fs.appendFileSync(LOG_PATH, (fresh ? '﻿' : '') + line + '\n', 'utf8');
  } catch { /* лог не критичен */ }
  try { console.log(line); } catch { /* нет консоли под планировщиком */ }
}

function parseEnvFile(fp) {
  const out = {};
  try {
    const txt = fs.readFileSync(fp, 'utf8');
    for (let line of txt.split(/\r?\n/)) {
      line = line.trim();
      if (!line || line.startsWith('#')) continue;
      const eq = line.indexOf('=');
      if (eq < 0) continue;
      const k = line.slice(0, eq).trim();
      let v = line.slice(eq + 1).trim();
      if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
      out[k] = v;
    }
  } catch { /* нет файла — вернём пусто */ }
  return out;
}

// Тот же канал, что у heartbeat бота: локальные env → .env бота → внешний файл
// из TELEGRAM_ENV_FILE. Секреты не дублируем.
function resolveTelegram() {
  let token = (process.env.WATCHDOG_TELEGRAM_BOT_TOKEN || process.env.TELEGRAM_BOT_TOKEN || '').trim();
  let chat = (process.env.WATCHDOG_TELEGRAM_CHAT_ID || process.env.TELEGRAM_CHAT_ID || '').trim();
  const botEnv = parseEnvFile(BOT_ENV);
  if (!token) token = (botEnv.TELEGRAM_BOT_TOKEN || '').trim();
  if (!chat) chat = (botEnv.TELEGRAM_CHAT_ID || '').trim();
  const ext = (botEnv.TELEGRAM_ENV_FILE || '').trim();
  if ((!token || !chat) && ext) {
    const extEnv = parseEnvFile(ext);
    if (!token) token = (extEnv.TELEGRAM_BOT_TOKEN || '').trim();
    if (!chat) chat = (extEnv.TELEGRAM_CHAT_ID || '').trim();
  }
  return { token, chat };
}

function sendTelegram(text) {
  const { token, chat } = resolveTelegram();
  if (!token || !chat) {
    log('Telegram ВЫКЛЮЧЕН: не найдены TELEGRAM_BOT_TOKEN/CHAT_ID (проверь TELEGRAM_ENV_FILE в .env бота).');
    return Promise.resolve(false);
  }
  const body = JSON.stringify({ chat_id: chat, text: `${PREFIX} ${text}`, disable_web_page_preview: true });
  return new Promise((resolve) => {
    const req = https.request({
      hostname: 'api.telegram.org',
      path: `/bot${token}/sendMessage`,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
      timeout: 12000,
    }, (res) => {
      let data = '';
      res.on('data', (c) => (data += c));
      res.on('end', () => {
        if (res.statusCode === 200) resolve(true);
        else { log(`Telegram ответил ${res.statusCode}: ${String(data).slice(0, 200)}`); resolve(false); }
      });
    });
    req.on('error', (e) => { log(`Telegram ошибка сети: ${e.message}`); resolve(false); });
    req.on('timeout', () => { req.destroy(); log('Telegram таймаут'); resolve(false); });
    req.write(body);
    req.end();
  });
}

function loadJson(fp, fallback) {
  try { return JSON.parse(fs.readFileSync(fp, 'utf8')); } catch { return fallback; }
}

function loadState() { return loadJson(STATE_PATH, {}); }
function saveState(s) {
  // --dry — предпросмотр: не трогаем реальный state (иначе dry-прогон «съедает»
  // суточный слот OK и сбивает анти-спам живого сторожа).
  if (DRY) { log('(--dry) состояние НЕ сохраняется.'); return; }
  try { fs.writeFileSync(STATE_PATH, JSON.stringify(s, null, 2), 'utf8'); }
  catch (e) { log(`не удалось сохранить state.json: ${e.message}`); }
}

// ─────────────────────────── чтение TV (CDP, read-only) ───────────────────────────

async function readLiveAlerts() {
  let list;
  try {
    const resp = await fetch(`http://${CDP_HOST}:${CDP_PORT}/json/list`);
    list = await resp.json();
  } catch (e) {
    const err = new Error(`CDP недоступен на ${CDP_HOST}:${CDP_PORT} (${e.message})`);
    err.code = 'TV_DOWN';
    throw err;
  }
  const target = list.find((t) => t.type === 'page' && /tradingview\.com\/chart/i.test(t.url))
    || list.find((t) => t.type === 'page' && /tradingview/i.test(t.url));
  if (!target) {
    const err = new Error('нет вкладки TradingView с графиком');
    err.code = 'TV_DOWN';
    throw err;
  }
  const client = await CDP({ host: CDP_HOST, port: CDP_PORT, target: target.id });
  try {
    await client.Runtime.enable();
    const expr = `
      fetch('https://pricealerts.tradingview.com/list_alerts', { credentials: 'include' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.s !== 'ok' || !Array.isArray(d.r)) return { error: (d.errmsg || 'unexpected response') };
          return { alerts: d.r.map(function (a) {
            var sym = a.symbol;
            try { sym = JSON.parse(String(a.symbol).replace(/^=/, '')).symbol || a.symbol; } catch (e) {}
            var inputs = null, pine_id = null, pine_version = null;
            try {
              var s = a.condition && a.condition.series;
              if (Array.isArray(s)) {
                for (var i = 0; i < s.length; i++) {
                  if (s[i] && s[i].inputs) { inputs = s[i].inputs; pine_id = s[i].pine_id || null; pine_version = s[i].pine_version || null; break; }
                }
              }
            } catch (e) {}
            return {
              alert_id: a.alert_id, symbol: sym, active: !!a.active,
              resolution: a.resolution, type: a.type, message: a.message,
              pine_id: pine_id, pine_version: pine_version, inputs: inputs
            };
          }) };
        })
        .catch(function (e) { return { error: e.message }; })
    `;
    const res = await client.Runtime.evaluate({ expression: expr, returnByValue: true, awaitPromise: true });
    if (res.exceptionDetails) {
      throw new Error('eval: ' + (res.exceptionDetails.exception?.description || res.exceptionDetails.text || 'unknown'));
    }
    const val = res.result?.value;
    if (!val || val.error) throw new Error('list_alerts: ' + (val?.error || 'пусто'));
    return val.alerts || [];
  } finally {
    try { await client.close(); } catch { /* уже закрыт */ }
  }
}

// ─────────────────────────── дифф ───────────────────────────

const LABELS = loadJson(LABELS_PATH, {});

function labelFor(pineId, key) {
  const map = (pineId && LABELS[pineId]) || null;
  const name = map && map[key];
  return name ? `${name} [${key}]` : key;
}

function inIndex(k) {
  const m = /^in_(\d+)$/.exec(k);
  return m ? Number(m[1]) : Number.MAX_SAFE_INTEGER;
}

function fmtVal(v) {
  if (v === undefined) return '(нет)';
  if (typeof v === 'string') {
    return v.length > 60 ? JSON.stringify(v.slice(0, 57) + '…') : JSON.stringify(v);
  }
  return JSON.stringify(v);
}

// Возвращает массив расхождений одного потока (пустой = OK).
function diffStream(etalon, live) {
  const issues = [];
  if (!live) {
    issues.push({ field: 'алерт', detail: `id ${etalon.alert_id} НЕ найден среди алертов TV` });
    return issues;
  }
  if (String(live.symbol) !== String(etalon.symbol)) {
    issues.push({ field: 'символ', was: etalon.symbol, now: live.symbol });
  }
  if (String(live.resolution) !== String(etalon.resolution)) {
    issues.push({ field: 'таймфрейм', was: etalon.resolution, now: live.resolution });
  }
  if (Boolean(live.active) !== Boolean(etalon.expected_active)) {
    issues.push({ field: 'активность (active)', was: etalon.expected_active, now: live.active });
  }
  if (etalon.pine_id && String(live.pine_id) !== String(etalon.pine_id)) {
    issues.push({ field: 'pine_id (сам скрипт)', was: etalon.pine_id, now: live.pine_id });
  }
  if (etalon.pine_version && String(live.pine_version) !== String(etalon.pine_version)) {
    issues.push({ field: 'версия скрипта', was: etalon.pine_version, now: live.pine_version });
  }
  const eIn = etalon.inputs || {};
  const lIn = live.inputs || {};
  const keys = Array.from(new Set([...Object.keys(eIn), ...Object.keys(lIn)]))
    .sort((a, b) => inIndex(a) - inIndex(b) || (a < b ? -1 : 1));
  for (const k of keys) {
    const inE = Object.prototype.hasOwnProperty.call(eIn, k);
    const inL = Object.prototype.hasOwnProperty.call(lIn, k);
    if (inE && inL) {
      if (JSON.stringify(eIn[k]) !== JSON.stringify(lIn[k])) {
        issues.push({ field: labelFor(etalon.pine_id, k), was: eIn[k], now: lIn[k] });
      }
    } else if (inE && !inL) {
      issues.push({ field: labelFor(etalon.pine_id, k), was: eIn[k], now: undefined });
    } else if (!inE && inL) {
      issues.push({ field: labelFor(etalon.pine_id, k) + ' (новый инпут)', was: undefined, now: lIn[k] });
    }
  }
  return issues;
}

// ─────────────────────────── режимы ───────────────────────────

async function runBaseline() {
  const live = await readLiveAlerts();
  const byId = new Map(live.map((a) => [Number(a.alert_id), a]));
  const streams = [];
  const missing = [];
  for (const s of STREAMS) {
    const a = byId.get(Number(s.alert_id));
    if (!a) { missing.push(s); continue; }
    streams.push({
      name: s.name,
      strategy: s.strategy,
      alert_id: s.alert_id,
      symbol: a.symbol,
      resolution: a.resolution,
      pine_id: a.pine_id,
      pine_version: a.pine_version,
      expected_active: s.expected_active,
      live_active_at_baseline: a.active,
      inputs: a.inputs || {},
    });
  }
  const etalons = {
    meta: {
      created: ts(),
      monitor: 'alert_frozen_inputs',
      note: 'READ-ONLY watchdog. inputs = замороженный снимок strategy-алерта (что реально стреляет). expected_active задаётся в манифесте watchdog.mjs, НЕ снимается с живого состояния. Пересобрать после осознанных изменений: node watchdog.mjs --baseline',
      cdp: `${CDP_HOST}:${CDP_PORT}`,
      stream_count: streams.length,
    },
    streams,
  };
  fs.writeFileSync(ETALONS_PATH, JSON.stringify(etalons, null, 2), 'utf8');
  log(`baseline: снято ${streams.length}/${STREAMS.length} потоков → ${ETALONS_PATH}`);
  for (const s of streams) {
    const warn = s.live_active_at_baseline !== s.expected_active
      ? `  ⚠️ сейчас active=${s.live_active_at_baseline}, эталон ждёт ${s.expected_active}` : '';
    log(`  • ${s.name}: alert ${s.alert_id}, ${s.symbol} ${s.resolution}, ${Object.keys(s.inputs).length} инпутов${warn}`);
  }
  if (missing.length) {
    log(`  ✗ НЕ найдены среди живых алертов: ${missing.map((m) => `${m.name} (${m.alert_id})`).join(', ')}`);
  }
  return etalons;
}

function buildReport(etalons, live) {
  const byId = new Map(live.map((a) => [Number(a.alert_id), a]));
  const perStream = [];
  let activeOk = 0;
  for (const et of etalons.streams) {
    const a = byId.get(Number(et.alert_id)) || null;
    const issues = diffStream(et, a);
    if (a && Boolean(a.active) === Boolean(et.expected_active)) activeOk++;
    perStream.push({ name: et.name, alert_id: et.alert_id, issues });
  }
  const drifted = perStream.filter((p) => p.issues.length > 0);
  return { perStream, drifted, total: etalons.streams.length, activeOk };
}

function formatDriftMessage(report) {
  const lines = [];
  lines.push(`⚠️ РАСХОЖДЕНИЕ КОНФИГА: ${report.drifted.length}/${report.total} поток(а/ов)`);
  for (const p of report.drifted) {
    lines.push('');
    lines.push(`▪ ${p.name} (alert ${p.alert_id})`);
    for (const it of p.issues) {
      if ('was' in it || 'now' in it) {
        lines.push(`   • ${it.field}: ${fmtVal(it.was)} → ${fmtVal(it.now)}`);
      } else {
        lines.push(`   • ${it.field}: ${it.detail}`);
      }
    }
  }
  lines.push('');
  lines.push('Проверь TV. Если изменение осознанное — пересними эталон: node watchdog.mjs --baseline');
  return lines.join('\n');
}

function formatConsole(report) {
  const out = [];
  for (const p of report.perStream) {
    if (!p.issues.length) { out.push(`  OK  ${p.name} (alert ${p.alert_id})`); continue; }
    out.push(`  ДИФФ ${p.name} (alert ${p.alert_id}):`);
    for (const it of p.issues) {
      if ('was' in it || 'now' in it) out.push(`         • ${it.field}: ${fmtVal(it.was)} → ${fmtVal(it.now)}`);
      else out.push(`         • ${it.field}: ${it.detail}`);
    }
  }
  return out.join('\n');
}

// Подпись набора расхождений для анти-спама (одинаковый дифф не долбит чаще ANTISPAM_MIN).
function driftSignature(report) {
  return JSON.stringify(report.drifted.map((p) => [p.alert_id, p.issues.map((i) => `${i.field}=${fmtVal(i.was)}>${fmtVal(i.now)}${i.detail || ''}`)]));
}

async function maybeSend(text, signature, state) {
  const now = Date.now();
  const sameAsLast = state.last_sig === signature;
  const recent = state.last_sig_ts && (now - state.last_sig_ts) < ANTISPAM_MIN * 60 * 1000;
  if (!FORCE && sameAsLast && recent) {
    log(`анти-спам: идентичное уведомление уже уходило <${ANTISPAM_MIN} мин назад — молчу.`);
    return { sent: false, suppressed: true };
  }
  if (DRY) { log('(--dry) Telegram НЕ отправляется. Текст ниже.'); log('\n' + text); return { sent: false, dry: true }; }
  const sent = await sendTelegram(text);
  state.last_sig = signature;
  state.last_sig_ts = now;
  return { sent };
}

async function runCheck() {
  const etalons = loadJson(ETALONS_PATH, null);
  if (!etalons || !Array.isArray(etalons.streams) || !etalons.streams.length) {
    log('НЕТ эталона: сначала запусти node watchdog.mjs --baseline и подтверди etalons.json.');
    return 2;
  }
  const state = loadState();

  let live;
  try {
    live = await readLiveAlerts();
  } catch (e) {
    if (e.code === 'TV_DOWN') {
      const msg = `⚠️ TV Desktop НЕДОСТУПЕН (${e.message}). Не могу проверить конфиги 6 боевых алертов. Запусти TradingView с CDP (порт ${CDP_PORT}).`;
      log('TV недоступен: ' + e.message);
      const sig = 'TV_DOWN';
      const r = await maybeSend(msg, sig, state);
      saveState(state);
      if (JSON_OUT) console.log(JSON.stringify({ ok: false, tv_down: true, sent: r.sent || false }));
      return 2;
    }
    log('Ошибка чтения алертов: ' + e.message);
    const r = await maybeSend(`⚠️ Watchdog: ошибка чтения алертов TV: ${e.message}`, 'READ_ERR:' + e.message, state);
    saveState(state);
    return 2;
  }

  const report = buildReport(etalons, live);
  log(`проверка: потоков ${report.total}, активность OK ${report.activeOk}/${report.total}, расхождений ${report.drifted.length}`);
  const consoleReport = formatConsole(report);
  if (consoleReport) log('\n' + consoleReport);

  if (report.drifted.length === 0) {
    // Чистый прогон — короткое OK не чаще раза в сутки.
    const today = localDate();
    if (state.last_ok_date !== today) {
      const msg = `✅ watchdog OK, ${report.activeOk}/${report.total} алертов активны, 0 расхождений.`;
      if (!DRY) { const sent = await sendTelegram(msg); log(`суточное OK: Telegram отправлен=${sent}`); }
      else { log('(--dry) daily OK НЕ отправляется:'); log(msg); }
      state.last_ok_date = today;
    } else {
      log('daily OK уже отправляли сегодня — молчу.');
    }
    state.last_sig = null;
    state.last_sig_ts = null;
    saveState(state);
    if (JSON_OUT) console.log(JSON.stringify({ ok: true, drifted: 0, active_ok: report.activeOk, total: report.total }));
    return 0;
  }

  // Есть расхождение → уведомление немедленно (с анти-спамом на идентичность).
  const msg = formatDriftMessage(report);
  const sig = driftSignature(report);
  const r = await maybeSend(msg, sig, state);
  saveState(state);
  log(`расхождение: Telegram отправлен=${r.sent || false}${r.suppressed ? ' (подавлено анти-спамом)' : ''}`);
  if (JSON_OUT) console.log(JSON.stringify({ ok: false, drifted: report.drifted.length, sent: r.sent || false, report: report.perStream }));
  return 1;
}

// ─────────────────────────── main ───────────────────────────

(async () => {
  if (MODE_TEST_TG) {
    const ok = await sendTelegram(`тест канала watchdog ${ts()} — если видишь это, уведомления настроены.`);
    log(`--test-telegram: отправлено=${ok}`);
    process.exit(ok ? 0 : 2);
  }
  if (MODE_BASELINE) {
    try { await runBaseline(); process.exit(0); }
    catch (e) {
      log('baseline ПРОВАЛЕН: ' + e.message);
      if (e.code === 'TV_DOWN') log('→ запусти TradingView Desktop с CDP и повтори.');
      process.exit(2);
    }
  }
  const code = await runCheck();
  process.exit(code);
})().catch((e) => { log('ФАТАЛЬНО: ' + (e && e.stack || e)); process.exit(2); });
