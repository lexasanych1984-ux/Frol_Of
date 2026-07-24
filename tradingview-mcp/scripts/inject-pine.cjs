// Заливка Pine-исходника с диска в Monaco-редактор TradingView через CDP.
// Использование: node inject-pine.cjs <targetId> <путь к .pine>
const CDP = require('C:/Users/lexas/.claude/tools/tradingview-mcp/node_modules/chrome-remote-interface');
const { readFileSync } = require('fs');

const FIND_MONACO = `
  (function findMonacoEditor() {
    var container = document.querySelector('.monaco-editor.pine-editor-monaco');
    if (!container) return null;
    var el = container;
    var fiberKey;
    for (var i = 0; i < 20; i++) {
      if (!el) break;
      fiberKey = Object.keys(el).find(function(k) { return k.startsWith('__reactFiber$'); });
      if (fiberKey) break;
      el = el.parentElement;
    }
    if (!fiberKey) return null;
    var current = el[fiberKey];
    for (var d = 0; d < 15; d++) {
      if (!current) break;
      if (current.memoizedProps && current.memoizedProps.value && current.memoizedProps.value.monacoEnv) {
        var env = current.memoizedProps.value.monacoEnv;
        if (env.editor && typeof env.editor.getEditors === 'function') {
          var editors = env.editor.getEditors();
          if (editors.length > 0) return { editor: editors[0], env: env };
        }
      }
      current = current.return;
    }
    return null;
  })()
`;

(async () => {
  const [targetId, file] = process.argv.slice(2);
  const source = readFileSync(file, 'utf8');
  const client = await CDP({ port: 9222, target: (targets) => targets.find(t => t.id === targetId) });
  try {
    const { Runtime } = client;
    await Runtime.enable();
    const expr = `(function() {
      var m = ${FIND_MONACO};
      if (!m) return JSON.stringify({ok:false, err:'no monaco'});
      var src = ${JSON.stringify(source)};
      var eds = m.env.editor.getEditors();
      var report = [];
      for (var i = 0; i < eds.length; i++) {
        var vis = eds[i].getDomNode() && eds[i].getDomNode().offsetParent !== null;
        eds[i].setValue(src);
        report.push({i: i, visible: vis, len: eds[i].getValue().length, hasMarker: eds[i].getValue().indexOf('v2.8n') !== -1});
      }
      return JSON.stringify({ok:true, srcLen: src.length, editors: report});
    })()`;
    const res = await Runtime.evaluate({ expression: expr, returnByValue: true });
    console.log(res.result.value || JSON.stringify(res.result));
  } finally {
    await client.close();
  }
})().catch(e => { console.error('FAIL: ' + e.message); process.exit(1); });
