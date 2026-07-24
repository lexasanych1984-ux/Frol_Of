// Чинит схлопнутые боксы: удаляет старые, грузит историю (setVisibleRange), рисует заново, проверяет ширину.
// node redraw-fix.cjs <targetId> <tradesFile> <sinceMs> <oldIdsJson|-> <outIdsJson>
const CDP = require('C:/Users/lexas/.claude/tools/tradingview-mcp/node_modules/chrome-remote-interface');
const { readFileSync, writeFileSync, existsSync } = require('fs');
const API = `window.TradingViewApi._activeChartWidgetWV.value()`;

const [targetId, file, sinceMsArg, oldIdsPath, outPath] = process.argv.slice(2);
const sinceMs = +sinceMsArg;

const seen = new Set();
const trades = [];
for (const line of readFileSync(file, 'utf8').split('\n')) {
  const t = line.trim();
  if (!t.startsWith('TRD|')) continue;
  const [, dir, entryMs, exitMs, entry, sl, tp] = t.split('|');
  if (+entryMs < sinceMs || seen.has(entryMs)) continue;
  seen.add(entryMs);
  trades.push({ t1: Math.floor(+entryMs / 1000), t2: Math.floor(Math.max(+exitMs, +entryMs + 8 * 3600e3) / 1000), entry: +entry, sl: +sl, tp: +tp });
}
const earliest = Math.min(...trades.map(t => t.t1));

(async () => {
  const client = await CDP({ port: 9222, target: ts => ts.find(t => t.id === targetId) });
  try {
    const { Runtime } = client;
    await Runtime.enable();
    const evalRB = async (expression, awaitPromise = false) => {
      const r = await Runtime.evaluate({ expression, returnByValue: true, awaitPromise });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
      return r.result.value;
    };
    // 1. удалить старые
    if (oldIdsPath !== '-' && existsSync(oldIdsPath)) {
      const old = JSON.parse(readFileSync(oldIdsPath, 'utf8')).ids || [];
      for (const id of old) await evalRB(`(function(){try{${API}.removeEntity('${id}')}catch(e){} return 1})()`);
      console.log('удалено старых: ' + old.length);
    }
    // 2. докачать историю пагинацией, пока первый загруженный бар не раньше (earliest - 7д)
    for (let i = 0; i < 30; i++) {
      const st = await evalRB(`(function(){
        var ms=${API}._chartWidget.model().mainSeries();
        var b=ms.bars(); var fv=b.valueAt(b.firstIndex());
        var more=true; try{more=ms.requestMoreDataAvailable()}catch(e){}
        return {first: fv && fv[0], more: more};
      })()`);
      console.log('история с: ' + (st.first ? new Date(st.first * 1000).toISOString().slice(0, 10) : '?'));
      if (!st || st.first == null || st.first <= earliest - 7 * 86400 || !st.more) break;
      await evalRB(`(function(){try{${API}._chartWidget.model().mainSeries().requestMoreData(1000)}catch(e){} return 1})()`);
      await new Promise(r => setTimeout(r, 1800));
    }
    // 3. нарисовать
    const before = new Set(await evalRB(`${API}.getAllShapes().map(function(s){return s.id})`));
    for (const tr of trades) {
      for (const b of [{ p1: tr.entry, p2: tr.tp, c: '#26a69a' }, { p1: tr.entry, p2: tr.sl, c: '#ef5350' }]) {
        await evalRB(`${API}.createMultipointShape([{time:${tr.t1},price:${b.p1}},{time:${tr.t2},price:${b.p2}}],{shape:"rectangle",overrides:{color:"${b.c}",backgroundColor:"${b.c}",transparency:82,linewidth:1}})`);
        await new Promise(r => setTimeout(r, 100));
      }
    }
    // 4. проверить ширины и собрать наши ids
    const check = await evalRB(`(function(){
      var a=${API}; var all=a.getAllShapes(); var before=${JSON.stringify([...before])};
      var ours=all.filter(function(s){return before.indexOf(s.id)<0});
      var zero=0, ok=0;
      for(var i=0;i<ours.length;i++){try{var p=a.getShapeById(ours[i].id).getPoints(); if(p[0].time===p[1].time)zero++;else ok++;}catch(e){}}
      return {ids:ours.map(function(s){return s.id}), zero:zero, ok:ok};
    })()`);
    console.log(`сделок ${trades.length}, боксов ${check.ids.length}: ширина OK ${check.ok}, схлопнуто ${check.zero}`);
    writeFileSync(outPath, JSON.stringify({ createdAt: new Date().toISOString(), targetId, ids: check.ids }, null, 1));
    // 5. сохранить лейаут
    await evalRB(`(function(){try{window.TradingViewApi.saveChart();return 1}catch(e){return 0}})()`);
    console.log('лейаут сохранён');
  } finally { await client.close(); }
})().catch(e => { console.error('FAIL: ' + e.message); process.exit(1); });
