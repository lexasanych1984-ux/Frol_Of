// Создаёт strategy-алерт «Исполнение ордера» для CRT Day 1H GER40 через внутренний REST TV.
// Структура — по образцу create-smc-alert.cjs (боевые алерты SMC/ASweep).
// Использование: node create-crt-alert.cjs <targetId>
// targetId — id вкладки с нужным графиком, взять из tab_list / CDP /json.
const path = require('path');
const CDP = require(path.join(__dirname, '..', 'node_modules', 'chrome-remote-interface'));

const TARGET_ID = process.argv[2];
if (!TARGET_ID) {
  console.log(JSON.stringify({ ok: false, err: 'usage: create-crt-alert.cjs <targetId>' }));
  process.exit(1);
}

const inputs = {
  in_0: true, in_1: 1773100800000, in_2: false, in_3: true, in_4: true, in_5: true, in_6: 3,
  in_7: true, in_8: true, in_9: 72, in_10: "1000-2300", in_11: "range", in_12: "rearm",
  in_13: "Europe/Moscow", in_14: true, in_15: false, in_16: 2.5, in_17: 1, in_18: 1, in_19: 60,
  in_20: false, in_21: false, in_22: "any", in_23: "dayExt", in_24: 1, in_25: true, in_26: false,
  in_27: 100000, in_28: 0, in_29: 0, in_30: 0, in_31: false, in_32: false, in_33: 0,
  in_34: "fixed", in_35: 1, in_36: "NONE", in_37: 0, in_38: "percent", in_39: 0, in_40: false,
  in_41: "FIFO", in_42: 2, in_43: false, in_44: false, in_45: "BACKTEST", in_46: "",
  in_47: "order_fills", in_48: "", in_49: true, in_50: "", in_51: false,
  pineFeatures: "{\"strategy\":1,\"plot\":1,\"str\":1,\"array\":1,\"ta\":1,\"math\":1,\"label\":1,\"table\":1,\"type\":1}",
  __profile: false
};

const payload = {
  conditions: [{
    type: "strategy",
    frequency: "60",
    series: [{
      type: "study",
      study: "StrategyScript@tv-scripting-101",
      pine_id: "USER;52d1b51daffe4a1999c592dea73647ef",
      pine_version: "21.0",
      inputs
    }],
    strategy_mode: "strategy",
    cross_interval: false,
    resolution: "60"
  }],
  symbol: '={"symbol":"FOREXCOM:GER40"}',
  resolution: "60",
  message: "{{strategy.order.alert_message}}",
  name: "CRT GER40 — исполнение ордера",
  sound_file: "alert/fired", sound_duration: 0,
  popup: true, auto_deactivate: false,
  email: false, sms_over_email: false, mobile_push: true,
  web_hook: null,
  expiration: null,
  active: true, ignore_warnings: true
};

(async () => {
  const client = await CDP({ port: 9222, target: ts => ts.find(t => t.id === TARGET_ID) });
  try {
    const { Runtime } = client;
    await Runtime.enable();
    const expr = `(function(){
      var x = new XMLHttpRequest();
      x.open('POST', 'https://pricealerts.tradingview.com/create_alert', false);
      x.withCredentials = true;
      x.setRequestHeader('Content-Type', 'text/plain;charset=UTF-8');
      x.send(JSON.stringify({ payload: ${JSON.stringify(payload)} }));
      return (x.responseText || '').slice(0, 400);
    })()`;
    const res = await Runtime.evaluate({ expression: expr, returnByValue: true });
    console.log(res.result.value || JSON.stringify(res));
  } finally { await client.close(); }
})().catch(e => { console.error('FAIL: ' + e.message); process.exit(1); });
