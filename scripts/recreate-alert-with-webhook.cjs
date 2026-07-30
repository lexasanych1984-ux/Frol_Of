// Пересоздаёт боевой strategy-алерт TradingView, ДОБАВЛЯЯ поле webhook, но не меняя
// параметры стратегии. Пуши в приложение сохраняются (mobile_push:true).
//
// Зачем отдельный generic-скрипт: шаблоны create-smc/create-crt содержат УСТАРЕВШИЕ
// захардкоженные inputs и не покрывают индексные (asweep) алерты. Здесь inputs берутся
// из АКТУАЛЬНОГО эталона watchdog (watchdog/etalons.json) — точный снимок того, что
// реально стреляет, — поэтому пересоздание не «дрейфит» стратегию.
//
// БЕЗОПАСНОСТЬ:
//   • ПЕРЕД запуском убедись, что эталон = живому состоянию:
//       node watchdog\watchdog.mjs --dry        (должно быть 6/6, 0 расхождений)
//     Если есть расхождение — сначала осознанно разберись/пересними (--baseline).
//   • Скрипт только СОЗДАЁТ новый алерт (с webhook). Старый НЕ удаляет — удали его сам
//     (MCP alert_delete или в приложении) ТОЛЬКО после того, как убедишься, что новый
//     активен, шлёт пуш и тело доходит до облака. До удаления оба алерта дают один и
//     тот же message — бот дедуплицирует по хэшу, двойного ордера не будет.
//   • После пересоздания всех 6: node watchdog\watchdog.mjs --baseline  (новые alert_id),
//     затем node watchdog\watchdog.mjs --dry  → подтвердить 6/6.
//
// Использование:
//   node scripts\recreate-alert-with-webhook.cjs --alert-id=5140264864 --webhook=https://<worker>.workers.dev/hook/<token>
//   node scripts\recreate-alert-with-webhook.cjs --alert-id=5140264864 --webhook=... --dry   (только показать payload)
//   node scripts\recreate-alert-with-webhook.cjs --list                                       (перечислить эталонные потоки)
//
// --pine-version=24.0 — пересоздать алерт на НОВОЙ версии скрипта. Нужен, когда скрипт
//   правили ради фикса: алерт приколот к версии на момент создания, а в эталоне лежит
//   старая (алерты-то ещё на ней и бегут), поэтому сама по себе правка в бой не попадёт.
//   Инпуты при этом остаются эталонные — правка не должна добавлять/убирать input(),
//   иначе индексы in_N сдвинутся и оверрайды станут ядовитыми.

const fs = require('fs');
const path = require('path');
const CDP = require('C:/Users/lexas/.claude/tools/tradingview-mcp/node_modules/chrome-remote-interface');

const ROOT = path.resolve(__dirname, '..');
const ETALONS = path.join(ROOT, 'watchdog', 'etalons.json');
const PORT = parseInt(process.env.TV_CDP_PORT || '9222', 10);

function arg(name) {
  const p = process.argv.find((a) => a.startsWith(`--${name}=`));
  return p ? p.split('=').slice(1).join('=') : undefined;
}
const flag = (name) => process.argv.includes(`--${name}`);

function loadStreams() {
  const j = JSON.parse(fs.readFileSync(ETALONS, 'utf8'));
  return j.streams || [];
}

function buildPayload(stream, webhook, pineVersion) {
  // Форма payload — по проверенным create-smc/create-crt рецептам; inputs/символ/TF/
  // pine — из эталона (живой снимок). Меняем только web_hook (было null) и, если задан
  // --pine-version, версию скрипта (см. шапку).
  return {
    conditions: [{
      type: 'strategy',
      frequency: '60',
      series: [{
        type: 'study',
        study: 'StrategyScript@tv-scripting-101',
        pine_id: stream.pine_id,
        pine_version: pineVersion || stream.pine_version,
        inputs: stream.inputs,
      }],
      strategy_mode: 'strategy',
      cross_interval: false,
      resolution: stream.resolution,
    }],
    symbol: `={"symbol":"${stream.symbol}"}`,
    resolution: stream.resolution,
    message: '{{strategy.order.alert_message}}',
    name: `${stream.name} (webhook)`,
    sound_file: 'alert/fired', sound_duration: 0,
    popup: true, auto_deactivate: false,
    email: false, sms_over_email: false,
    mobile_push: true,          // ПУШИ В ПРИЛОЖЕНИЕ СОХРАНЯЮТСЯ
    web_hook: webhook,          // ← единственное отличие от текущего алерта
    expiration: null,
    active: true, ignore_warnings: true,
  };
}

async function main() {
  const streams = loadStreams();

  if (flag('list')) {
    for (const s of streams) {
      console.log(`${s.alert_id}\t${s.name}\t${s.symbol}\t${s.resolution}\t${s.strategy}`);
    }
    return;
  }

  const alertId = arg('alert-id');
  const webhook = arg('webhook');
  if (!alertId || (!webhook && !flag('dry'))) {
    console.error('Нужно: --alert-id=<id> --webhook=<url>  (или --list, или добавь --dry).');
    process.exit(2);
  }
  const stream = streams.find((s) => String(s.alert_id) === String(alertId));
  if (!stream) {
    console.error(`Эталон для alert_id=${alertId} не найден в ${ETALONS}. --list покажет доступные.`);
    process.exit(2);
  }

  const pineVersion = arg('pine-version');
  const payload = buildPayload(stream, webhook || 'https://EXAMPLE/hook/TOKEN', pineVersion);
  if (pineVersion) {
    console.log(`Версия скрипта: эталонная ${stream.pine_version} → ${pineVersion} (--pine-version).`);
  }
  if (flag('dry')) {
    console.log(`[DRY] ${stream.name} (${stream.symbol} ${stream.resolution}) — payload:`);
    console.log(JSON.stringify({ payload }, null, 2));
    console.log('\n[DRY] Ничего не отправлено. Убери --dry и задай --webhook для создания.');
    return;
  }

  const client = await CDP({
    port: PORT,
    target: (ts) => ts.find((t) => t.type === 'page' && /tradingview/i.test(t.url || ''))
      || ts.find((t) => t.webSocketDebuggerUrl),
  });
  try {
    const { Runtime } = client;
    await Runtime.enable();
    const expr = `(function(){
      var x = new XMLHttpRequest();
      x.open('POST', 'https://pricealerts.tradingview.com/create_alert', false);
      x.withCredentials = true;
      x.setRequestHeader('Content-Type', 'text/plain;charset=UTF-8');
      x.send(JSON.stringify({ payload: ${JSON.stringify(payload)} }));
      return (x.responseText || '').slice(0, 600);
    })()`;
    const res = await Runtime.evaluate({ expression: expr, returnByValue: true });
    const text = res.result.value || JSON.stringify(res);
    console.log(`Ответ create_alert для «${stream.name}»:`);
    console.log(text);
    // Успех create_alert = {"s":"ok", ...}. Числовой alert_id приходит в r.id;
    // ответ может быть длинным — при обрезке r.id не видно, но s:ok = создано.
    let parsed = null;
    try { parsed = JSON.parse(text); } catch (_) { /* ответ мог обрезаться */ }
    const okByJson = parsed && parsed.s === 'ok';
    const okByText = /"s"\s*:\s*"ok"/.test(text);
    if (okByJson || okByText) {
      const newId = parsed && parsed.r && (parsed.r.id || parsed.r.alert_id);
      const wh = parsed && parsed.r && parsed.r.web_hook;
      console.log(`\n✅ Алерт СОЗДАН (s=ok)${newId ? `, alert_id=${newId}` : ''}.`);
      if (wh) console.log(`   web_hook в ответе: ${wh}`);
      console.log(`   Проверь: активен, symbol/TF/inputs те же, webhook на месте, пуш приходит.`);
      console.log(`   ТОЛЬКО ПОСЛЕ этого удали старый alert_id=${alertId} (MCP alert_delete / в приложении).`);
    } else {
      const code = parsed && parsed.err && parsed.err.code;
      console.log(`\n⚠️ create_alert НЕ ok${code ? ` (${code})` : ''} — алерт НЕ создан. Разберись перед удалением старого.`);
    }
  } finally {
    await client.close();
  }
}

main().catch((e) => { console.error('FAIL: ' + e.message); process.exit(1); });
