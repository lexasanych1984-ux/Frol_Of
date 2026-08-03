// Пересоздаёт боевой strategy-алерт TradingView, меняя РОВНО ОДИН инпут стратегии.
//
// Зачем отдельно от recreate-alert-with-webhook.cjs: тот собирает payload из эталона
// вотчдога и переписывает имя (дописывает «(webhook)»). Здесь источник — САМ ЖИВОЙ АЛЕРТ
// через list_alerts: копируются символ (вместе с adjustment), TF, pine_id/версия, ВСЕ
// инпуты включая лицензионный блоб `text`, имя, webhook, push, звук, message. Меняется
// только заданный in_N. Так исключён дрейф из-за расхождения эталона с живым состоянием.
//
// БЕЗОПАСНОСТЬ: скрипт только СОЗДАЁТ новый алерт. Старый НЕ удаляет — удалить вручную
// после проверки нового. Пока оба живы, оба шлют одинаковый message.
//
// Использование:
//   node scripts\clone-alert-with-input-change.cjs --alert-id=5272544462 --set=in_27=30 --dry
//   node scripts\clone-alert-with-input-change.cjs --alert-id=5272544462 --set=in_27=30
const CDP = require('C:/Users/lexas/.claude/tools/tradingview-mcp/node_modules/chrome-remote-interface');

const arg = (n) => {
  const p = process.argv.find((a) => a.startsWith(`--${n}=`));
  return p ? p.split('=').slice(1).join('=') : undefined;
};
const flag = (n) => process.argv.includes(`--${n}`);

const alertId = arg('alert-id');
const setArg = arg('set');
if (!alertId || !setArg) {
  console.error('Нужно: --alert-id=<id> --set=in_N=<значение>  [--dry]');
  process.exit(2);
}
const m = /^(in_\d+)=(.*)$/.exec(setArg);
if (!m) { console.error('--set должен быть вида in_27=30'); process.exit(2); }
const inputKey = m[1];
// число остаётся числом, true/false — булевым, всё прочее — строкой
let inputVal = m[2];
if (/^-?\d+(\.\d+)?$/.test(inputVal)) inputVal = parseFloat(inputVal);
else if (inputVal === 'true') inputVal = true;
else if (inputVal === 'false') inputVal = false;

const FETCH = (id) => `(async function(){
  var r = await fetch('https://pricealerts.tradingview.com/list_alerts', {credentials:'include'});
  var j = await r.json();
  var arr = (j && (j.r || j.alerts || j.d)) || [];
  var a = arr.find(function(x){ return String(x.alert_id||x.id) === '${id}'; });
  return JSON.stringify(a || null);
})()`;

const CREATE = (payload) => `(function(){
  var x = new XMLHttpRequest();
  x.open('POST', 'https://pricealerts.tradingview.com/create_alert', false);
  x.withCredentials = true;
  x.setRequestHeader('Content-Type', 'text/plain;charset=UTF-8');
  x.send(JSON.stringify({ payload: ${JSON.stringify(payload)} }));
  return (x.responseText || '').slice(0, 600);
})()`;

(async () => {
  const client = await CDP({ port: 9222, target: (ts) => ts.find((t) => t.type === 'page' && /tradingview/i.test(t.url || '')) });
  try {
    const { Runtime } = client;
    await Runtime.enable();
    const got = await Runtime.evaluate({ expression: FETCH(alertId), returnByValue: true, awaitPromise: true });
    const src = JSON.parse(got.result.value || 'null');
    if (!src) { console.error(`Алерт ${alertId} не найден в list_alerts.`); process.exit(1); }

    const cond = JSON.parse(JSON.stringify(src.conditions ? src.conditions[0] : src.condition));
    const inputs = cond.series[0].inputs;
    if (!(inputKey in inputs)) { console.error(`У алерта нет инпута ${inputKey}.`); process.exit(1); }
    const before = inputs[inputKey];
    inputs[inputKey] = inputVal;

    const payload = {
      conditions: [cond],
      symbol: src.symbol,
      resolution: src.resolution,
      message: src.message,
      name: src.name,
      sound_file: src.sound_file, sound_duration: src.sound_duration,
      popup: src.popup, auto_deactivate: src.auto_deactivate,
      email: src.email, sms_over_email: src.sms_over_email,
      mobile_push: src.mobile_push,
      web_hook: src.web_hook,
      expiration: src.expiration,
      active: true, ignore_warnings: true,
    };

    console.log(`Источник: alert_id=${alertId} «${src.name}» ${src.symbol} ${src.resolution}`);
    console.log(`  pine: ${cond.series[0].pine_id} ver ${cond.series[0].pine_version}`);
    const whShort = src.web_hook ? src.web_hook.split('/hook/')[0] + '/hook/…' : 'НЕТ (!)';
    console.log(`  webhook: ${whShort} | push: ${src.mobile_push}`);
    console.log(`  правка: ${inputKey} ${JSON.stringify(before)} → ${JSON.stringify(inputVal)}`);
    console.log(`  инпутов всего: ${Object.keys(inputs).length}`);
    if (!src.web_hook) {
      console.log('\n⚠️ У исходного алерта нет webhook — создавать нельзя, разберись.');
      process.exit(1);
    }
    if (flag('dry')) {
      console.log('\n[DRY] ничего не отправлено.');
      return;
    }

    const res = await Runtime.evaluate({ expression: CREATE(payload), returnByValue: true });
    const text = res.result.value || '';
    console.log('\nОтвет create_alert:');
    console.log(text.slice(0, 400));
    let parsed = null;
    try { parsed = JSON.parse(text); } catch (_) {}
    const ok = (parsed && parsed.s === 'ok') || /"s"\s*:\s*"ok"/.test(text);
    if (ok) {
      const newId = parsed && parsed.r && (parsed.r.id || parsed.r.alert_id);
      console.log(`\n✅ СОЗДАН${newId ? `, alert_id=${newId}` : ''}. Старый ${alertId} НЕ удалён — удалить вручную после проверки.`);
    } else {
      console.log('\n⚠️ create_alert НЕ ok — алерт не создан.');
      process.exit(1);
    }
  } finally { await client.close(); }
})().catch((e) => { console.error('FAIL: ' + e.message); process.exit(1); });
