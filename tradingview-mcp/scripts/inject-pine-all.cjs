// Заливка Pine-исходника во ВСЕ инстансы Monaco (видимый + невидимый),
// обход глюка pine_set_source «Could not open Pine Editor» / невидимого инстанса.
// Использование: node inject-pine-all.cjs <targetId> <путь-к-файлу.pine>
const fs = require('fs');
const path = require('path');
const CDP = require(path.join(__dirname, '..', 'node_modules', 'chrome-remote-interface'));

const [, , targetId, pineFile] = process.argv;
if (!targetId || !pineFile) {
  console.log(JSON.stringify({ ok: false, err: 'usage: inject-pine-all.cjs <targetId> <file>' }));
  process.exit(1);
}

(async () => {
  let client;
  try {
    const source = fs.readFileSync(pineFile, 'utf8');
    client = await CDP({ target: targetId, port: 9222 });
    const { Runtime } = client;
    const expr = `(function(){
      var containers = Array.from(document.querySelectorAll('.monaco-editor.pine-editor-monaco'));
      if (!containers.length) return JSON.stringify({ok:false, err:'no container'});
      var env = null;
      for (var ci = 0; ci < containers.length && !env; ci++) {
        var el = containers[ci], fiberKey = null;
        for (var i = 0; i < 20; i++) {
          if (!el) break;
          fiberKey = Object.keys(el).find(function(k){ return k.indexOf('__reactFiber$') === 0; });
          if (fiberKey) break;
          el = el.parentElement;
        }
        if (!fiberKey) continue;
        var current = el[fiberKey];
        for (var d = 0; d < 15; d++) {
          if (!current) break;
          if (current.memoizedProps && current.memoizedProps.value && current.memoizedProps.value.monacoEnv) {
            env = current.memoizedProps.value.monacoEnv;
            break;
          }
          current = current.return;
        }
      }
      if (!env) return JSON.stringify({ok:false, err:'no env in any container'});
      var eds = env.editor.getEditors();
      if (!eds.length) return JSON.stringify({ok:false, err:'no editors'});
      var src = ${JSON.stringify(source)};
      var set = 0, visSet = false;
      eds.forEach(function(ed){
        ed.setValue(src);
        set++;
        var dn = ed.getDomNode();
        if (dn && dn.offsetParent !== null) visSet = true;
      });
      return JSON.stringify({ok:true, editors_set:set, visible_included:visSet, len:src.length});
    })()`;
    const res = await Runtime.evaluate({ expression: expr, returnByValue: true });
    console.log(res.result.value);
  } catch (e) {
    console.log(JSON.stringify({ ok: false, err: String(e.message || e) }));
  } finally {
    if (client) await client.close();
  }
})();
