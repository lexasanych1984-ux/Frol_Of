// Рисует сделки (TRD-строки) прямоугольниками на активном графике вкладки TradingView.
// Использование: node batch-draw-trades.cjs <targetId> <файл с TRD> <sinceMs> [outIdsFile]
// Зелёный бокс entry→tp, красный entry→sl; правый край не короче entry+6ч (иначе схлопнется на H4).
const CDP = require('C:/Users/lexas/.claude/tools/tradingview-mcp/node_modules/chrome-remote-interface');
const { readFileSync, writeFileSync } = require('fs');

const [targetId, file, sinceMsArg, outFile] = process.argv.slice(2);
const sinceMs = +sinceMsArg;
const API = `window.TradingViewApi._activeChartWidgetWV.value()`;

const seen = new Set();
const trades = [];
for (const line of readFileSync(file, 'utf8').split('\n')) {
  const t = line.trim();
  if (!t.startsWith('TRD|')) continue;
  const [, dir, entryMs, exitMs, entry, sl, tp] = t.split('|');
  if (+entryMs < sinceMs || seen.has(entryMs)) continue;
  seen.add(entryMs);
  trades.push({ dir, t1: Math.floor(+entryMs / 1000), t2: Math.floor(Math.max(+exitMs, +entryMs + 6 * 3600e3) / 1000), entry: +entry, sl: +sl, tp: +tp });
}

(async () => {
  const client = await CDP({ port: 9222, target: (ts) => ts.find(t => t.id === targetId) });
  try {
    const { Runtime } = client;
    await Runtime.enable();
    const ids = [];
    for (const tr of trades) {
      const boxes = [
        { p1: tr.entry, p2: tr.tp, color: '#26a69a' }, // профит-зона
        { p1: tr.entry, p2: tr.sl, color: '#ef5350' }, // риск-зона
      ];
      for (const b of boxes) {
        const expr = `(function(){
          var api = ${API};
          var id = api.createMultipointShape(
            [{ time: ${tr.t1}, price: ${b.p1} }, { time: ${tr.t2}, price: ${b.p2} }],
            { shape: "rectangle", overrides: { color: "${b.color}", backgroundColor: "${b.color}", transparency: 82, linewidth: 1 } }
          );
          return id;
        })()`;
        const res = await Runtime.evaluate({ expression: expr, returnByValue: true });
        if (res.exceptionDetails) { console.error('DRAW FAIL: ' + JSON.stringify(res.exceptionDetails.exception?.description || res.exceptionDetails.text)); process.exit(1); }
        ids.push(res.result.value);
        await new Promise(r => setTimeout(r, 120));
      }
    }
    console.log(JSON.stringify({ trades: trades.length, shapes: ids.length, ids }));
    if (outFile) writeFileSync(outFile, JSON.stringify({ createdAt: new Date().toISOString(), targetId, ids }, null, 1));
  } finally { await client.close(); }
})().catch(e => { console.error('FAIL: ' + e.message); process.exit(1); });
