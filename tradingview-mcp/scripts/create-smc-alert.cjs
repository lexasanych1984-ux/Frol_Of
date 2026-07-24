// Создаёт strategy-алерт «Исполнение ордера» для SMC EURUSD через внутренний REST TV
// (структура condition скопирована с работающих алертов NAS100/GER40 из alert_list).
const CDP = require('C:/Users/lexas/.claude/tools/tradingview-mcp/node_modules/chrome-remote-interface');

const inputs = {
  in_0: 1, in_1: 3, in_2: "240", in_3: 2, in_4: 2, in_5: 1, in_6: true, in_7: true, in_8: true,
  in_9: 42, in_10: false, in_11: true, in_12: 1, in_13: true, in_14: 1.5, in_15: 20, in_16: true,
  in_17: true, in_18: true, in_19: "0800-1700", in_20: "0800-1700", in_21: false, in_22: true,
  in_23: 0, in_24: 30, in_25: true, in_26: true, in_27: 20, in_28: 60, in_29: true, in_30: 0.4,
  in_31: 1.2, in_32: 10000, in_33: 0, in_34: 0, in_35: 0, in_36: false, in_37: true, in_38: 5,
  in_39: false, in_40: 0, in_41: "fixed", in_42: 1, in_43: "NONE", in_44: "percent", in_45: 0,
  in_46: "FIFO", in_47: 2, in_48: false, in_49: false, in_50: "BACKTEST", in_51: "",
  in_52: "order_fills", in_53: "", in_54: true, in_55: "", in_56: false,
  pineFeatures: "{\"strategy\":1,\"str\":1,\"array\":1,\"ta\":1,\"math\":1,\"line\":1,\"box\":1,\"label\":1,\"table\":1,\"request.security\":1}",
  __profile: false
};

const payload = {
  conditions: [{
    type: "strategy",
    frequency: "60",
    series: [{
      type: "study",
      study: "StrategyScript@tv-scripting-101",
      pine_id: "USER;89bffcd505124ef6bde6498a57d4002d",
      pine_version: "23.0",
      inputs
    }],
    strategy_mode: "strategy",
    cross_interval: false,
    resolution: "15"
  }],
  symbol: '={"symbol":"FX:EURUSD"}',
  resolution: "15",
  message: "{{strategy.order.alert_message}}",
  name: "SMC EURUSD — исполнение ордера",
  sound_file: "alert/fired", sound_duration: 0,
  popup: true, auto_deactivate: false,
  email: false, sms_over_email: false, mobile_push: true,
  web_hook: null,
  expiration: null,
  active: true, ignore_warnings: true
};

(async () => {
  const client = await CDP({ port: 9222, target: ts => ts.find(t => t.id === '0B111930869F06A44B16D15B8A81A148') });
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
