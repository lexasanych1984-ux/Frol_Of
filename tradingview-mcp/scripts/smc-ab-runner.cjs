// A/B-прогон SMC по одному окну: докачка истории один раз, затем переключение
// вариантов инпутами с ожиданием стабилизации отчёта.
//
// Отличия от msnr-ab-runner.cjs:
//   - снимает пер-сделочный список (reportData().trades) — нужен для разбора срезанных сделок;
//   - маркер конфига ищется по подстроке в ЛЮБОЙ строке Pine-таблицы, а не только в первой
//     (у SMC таблица большая, «Стоп-коридор | блок L/S» лежит в середине);
//   - комиссию не трогает (в боевой конфигурации SMC она 0).
//
// Использование:
//   node smc-ab-runner.cjs <targetId> <entityId> <expectSymbol> <variantsJson> <outFile>
// variantsJson: [{"name":"base","expectMarker":"20–60п","inputs":{"in_27":20}}, ...]
const path = require('path');
const fs = require('fs');
const CDP = require(path.join(__dirname, '..', 'node_modules', 'chrome-remote-interface'));

const [, , targetId, entityId, expectSymbol, variantsArg, outFile] = process.argv;
if (!targetId || !entityId || !variantsArg) {
  console.log(JSON.stringify({ ok: false, err: 'usage: smc-ab-runner.cjs <targetId> <entityId> <expectSymbol> <variantsJson> <outFile>' }));
  process.exit(1);
}
// variantsArg может быть путём к файлу через '@' — так кириллица в expectMarker
// не проходит через кодировку командной строки Windows.
const variants = JSON.parse(
  variantsArg[0] === '@' ? fs.readFileSync(variantsArg.slice(1), 'utf8') : variantsArg
);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const SET_INPUTS = (id, obj) => `(function(){
  try {
    var chart = window.TradingViewApi.activeChart();
    var st = chart.getStudyById(${JSON.stringify(id)});
    if (!st) return JSON.stringify({err:'study not found'});
    var cur = st.getInputValues();
    var ov = ${JSON.stringify(obj)};
    var done = {};
    for (var i = 0; i < cur.length; i++) {
      if (Object.prototype.hasOwnProperty.call(ov, cur[i].id)) { cur[i].value = ov[cur[i].id]; done[cur[i].id] = ov[cur[i].id]; }
    }
    st.setInputValues(cur);
    return JSON.stringify({ok:true, set:done});
  } catch(e) { return JSON.stringify({err:String(e).slice(0,160)}); }
})()`;

const READ_INPUTS = (id) => `(function(){
  try {
    var st = window.TradingViewApi.activeChart().getStudyById(${JSON.stringify(id)});
    var cur = st.getInputValues(), o = {};
    for (var i = 0; i < cur.length; i++) o[cur[i].id] = cur[i].value;
    return JSON.stringify(o);
  } catch(e) { return JSON.stringify({err:String(e).slice(0,160)}); }
})()`;

const PULL = `(function(){
  try {
    var s = window.TradingViewApi._activeChartWidgetWV.value()._chartWidget.model().mainSeries();
    s.requestMoreData(10000);
    return JSON.stringify({bars: s.bars().size()});
  } catch(e) { return JSON.stringify({err:String(e).slice(0,120)}); }
})()`;

const PROBE = (id, withTrades) => `(function(){
  try {
    var cw = window.TradingViewApi._activeChartWidgetWV.value()._chartWidget;
    var m = cw.model(), s = m.mainSeries();
    var ds = m.dataSources().find(function(x){
      try { return typeof x.reportData === 'function' && x.id && x.id() === ${JSON.stringify(id)}; } catch(e){ return false; }
    });
    if (!ds) return JSON.stringify({err:'strategy not found'});
    var rd = ds.reportData(); if (rd && typeof rd.value === 'function') rd = rd.value();
    var p = rd && rd.performance;
    var pick = function(o){ return o ? {
      trades: (o.numberOfWiningTrades||0)+(o.numberOfLosingTrades||0),
      win: o.numberOfWiningTrades||0, loss: o.numberOfLosingTrades||0,
      net: o.netProfit, netPct: o.netProfitPercent, pf: o.profitFactor,
      gp: o.grossProfit, gl: o.grossLoss, largestWin: o.largestWinTrade, largestLoss: o.largestLosTrade,
      commission: o.commissionPaid
    } : null; };
    var rows = [];
    try {
      var g = ds._graphics, pc = g && g._primitivesCollection;
      var outer = pc && pc.dwgtablecells;
      var inner = outer && outer.get('tableCells');
      var coll = null;
      if (inner) {
        if (inner._primitivesDataById) coll = inner;
        else if (typeof inner.get === 'function') { try { coll = inner.get(false); } catch(e) {} }
      }
      if (coll && coll._primitivesDataById) {
        var cells = [];
        coll._primitivesDataById.forEach(function(v){ cells.push(v); });
        cells.sort(function(a,b){ return (a.row||0)-(b.row||0); });
        for (var c = 0; c < cells.length; c++) if (cells[c].t) rows.push(cells[c].t);
      }
    } catch(e) {}
    var trades = null;
    if (${withTrades ? 'true' : 'false'} && rd && rd.trades) {
      trades = rd.trades.map(function(t){
        return {
          ec: t.e && t.e.c, ep: t.e && t.e.p, etm: t.e && t.e.tm,
          xc: t.x && t.x.c, xp: t.x && t.x.p, xtm: t.x && t.x.tm,
          q: t.q, pnl: t.tp && t.tp.v, cum: t.cp && t.cp.v, dd: t.dd && t.dd.v, cm: t.cm
        };
      });
    }
    var bb = s.bars();
    return JSON.stringify({
      symbol: s.symbolInfo() ? s.symbolInfo().full_name : null,
      bars: bb.size(), more: s.requestMoreDataAvailable(),
      first: bb.firstIndex && bb.firstIndex(), last: bb.lastIndex && bb.lastIndex(),
      all: p ? pick(p.all) : null, long: p ? pick(p.long) : null, short: p ? pick(p.short) : null,
      ddPct: p ? p.maxStrategyDrawDownPercent : null,
      rows: rows, trades: trades
    });
  } catch(e) { return JSON.stringify({err:String(e).slice(0,160)}); }
})()`;

// Ячейка «Стоп-коридор | блок L/S» — единственная строка таблицы вида «20–60п | 2 / 4».
// По ней проверяется, что инпут реально доехал до пересчёта, и по ней же стабилизация:
// брать всю таблицу в подпись нельзя — там есть живые уровни ликвидности.
const bandOf = (rows) => {
  if (!rows) return '';
  for (const r of rows) if (/\d+\D\d+п/.test(String(r))) return String(r);
  return '';
};

(async () => {
  let client;
  try {
    client = await CDP({ target: targetId, port: 9222 });
    const { Runtime } = client;
    const run = async (expr) => {
      const r = await Runtime.evaluate({ expression: expr, returnByValue: true });
      return JSON.parse(r.result.value);
    };

    // --- докачка истории (один раз на окно)
    let st = await run(PROBE(entityId, false));
    if (st.err) throw new Error(st.err);
    let stall = 0;
    for (let i = 0; i < 40; i++) {
      const prev = st.bars;
      await run(PULL);
      await sleep(3000);
      st = await run(PROBE(entityId, false));
      if (st.bars <= prev) {
        stall++;
        await sleep(2000);
        st = await run(PROBE(entityId, false));
        if (st.bars > prev) stall = 0;
        if (stall >= 3) break;
      } else stall = 0;
    }
    const bars = st.bars;
    const symbol = st.symbol;

    const out = [];
    for (const v of variants) {
      const sres = await run(SET_INPUTS(entityId, v.inputs));
      if (sres.err) throw new Error('set inputs: ' + sres.err);
      const applied = Object.keys(sres.set || {});
      if (applied.length !== Object.keys(v.inputs).length) {
        throw new Error('inputs not applied: ' + JSON.stringify(sres.set));
      }
      await sleep(2500);

      let prevSig = null, stableFor = 0, settled = false, cur = null;
      for (let i = 0; i < 40; i++) {
        cur = await run(PROBE(entityId, false));
        const band = bandOf(cur.rows);
        const mkOk = !v.expectMarker || band.indexOf(v.expectMarker) !== -1;
        const sig = JSON.stringify([cur.bars, cur.all && cur.all.trades, cur.all && cur.all.net, cur.ddPct, band]);
        if (sig === prevSig && mkOk) {
          stableFor++;
          if (stableFor >= 3) { settled = true; break; }
        } else stableFor = 0;
        prevSig = sig;
        await sleep(2500);
      }
      // финальный забор — уже со сделками
      const full = await run(PROBE(entityId, true));
      const band = bandOf(full.rows);
      out.push({
        variant: v.name, settled,
        markerOk: v.expectMarker ? band.indexOf(v.expectMarker) !== -1 : null,
        marker: (full.rows || []).join(' ~ '), band,
        rows: full.rows, all: full.all, long: full.long, short: full.short,
        ddPct: full.ddPct, bars: full.bars, trades: full.trades,
        inputs: await run(READ_INPUTS(entityId))
      });
    }

    const payload = {
      ok: true, symbol, expectSymbol: expectSymbol || null,
      symbolOk: expectSymbol ? symbol === expectSymbol : null,
      bars, results: out
    };
    if (outFile) {
      fs.writeFileSync(outFile, JSON.stringify(payload, null, 1));
      console.log(JSON.stringify({
        ok: true, symbol, bars, out: outFile,
        summary: out.map((r) => ({
          v: r.variant, settled: r.settled, markerOk: r.markerOk,
          tr: r.all && r.all.trades, w: r.all && r.all.win, l: r.all && r.all.loss,
          net: r.all && +r.all.net.toFixed(2), pf: r.all && +r.all.pf.toFixed(3),
          gl: r.all && +r.all.gl.toFixed(2), dd: +(r.ddPct * 100).toFixed(2),
          band: r.band
        }))
      }, null, 1));
    } else {
      console.log(JSON.stringify(payload, null, 1));
    }
  } catch (e) {
    console.log(JSON.stringify({ ok: false, err: String(e.message || e) }));
  } finally {
    if (client) await client.close();
  }
})();
