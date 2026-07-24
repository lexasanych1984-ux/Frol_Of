// Выгружает исходник из Monaco-редактора TradingView в файл.
// Использование: node dump-pine.cjs <targetId> <выходной файл>
const CDP = require('C:/Users/lexas/.claude/tools/tradingview-mcp/node_modules/chrome-remote-interface');
const { writeFileSync } = require('fs');

const FIND = `(function(){var c=document.querySelector('.monaco-editor.pine-editor-monaco');if(!c)return null;var el=c,fk;for(var i=0;i<20;i++){if(!el)break;fk=Object.keys(el).find(function(k){return k.indexOf('__reactFiber$')===0});if(fk)break;el=el.parentElement}if(!fk)return null;var cur=el[fk];for(var d=0;d<15;d++){if(!cur)break;if(cur.memoizedProps&&cur.memoizedProps.value&&cur.memoizedProps.value.monacoEnv){var env=cur.memoizedProps.value.monacoEnv;if(env.editor&&typeof env.editor.getEditors==='function'){var eds=env.editor.getEditors();if(eds.length>0)return eds[0].getValue()}}cur=cur.return}return null})()`;

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
