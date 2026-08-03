// Выгружает исходник из Monaco-редактора TradingView в файл.
// Использование: node dump-pine.cjs <targetId> <выходной файл>
const CDP = require('C:/Users/lexas/.claude/tools/tradingview-mcp/node_modules/chrome-remote-interface');
const { writeFileSync } = require('fs');

// Берём инстанс с САМЫМ ДЛИННЫМ содержимым, а не getEditors()[0]: нулевой бывает
// пустым (после переоткрытия панели Pine их несколько), и скрипт падал с
// «editor not found» на живом редакторе с кодом.
const FIND = `(function(){var c=document.querySelector('.monaco-editor.pine-editor-monaco');if(!c)return null;var el=c,fk;for(var i=0;i<25;i++){if(!el)break;fk=Object.keys(el).find(function(k){return k.indexOf('__reactFiber$')===0});if(fk)break;el=el.parentElement}if(!fk)return null;var cur=el[fk];for(var d=0;d<60;d++){if(!cur)break;if(cur.memoizedProps&&cur.memoizedProps.value&&cur.memoizedProps.value.monacoEnv){var env=cur.memoizedProps.value.monacoEnv;if(env.editor&&typeof env.editor.getEditors==='function'){var best='';env.editor.getEditors().forEach(function(ed){var v=ed.getValue()||'';if(v.length>best.length)best=v});return best||null}}cur=cur.return}return null})()`;

(async () => {
  const [targetId, out] = process.argv.slice(2);
  const client = await CDP({ port: 9222, target: ts => ts.find(t => t.id === targetId) });
  try {
    const { Runtime } = client;
    await Runtime.enable();
    const res = await Runtime.evaluate({ expression: FIND, returnByValue: true });
    const src = res.result.value;
    if (!src) { console.error('FAIL: editor not found'); process.exit(1); }
    writeFileSync(out, src);
    console.log('dumped ' + src.length + ' chars, ' + src.split('\n').length + ' lines → ' + out);
  } finally { await client.close(); }
})().catch(e => { console.error('FAIL: ' + e.message); process.exit(1); });
