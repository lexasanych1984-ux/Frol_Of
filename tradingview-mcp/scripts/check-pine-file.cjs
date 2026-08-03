#!/usr/bin/env node
// Компиляция .pine файла серверным API TradingView — без графика, редактора и Save.
// То же, что MCP-инструмент pine_check, но берёт исходник С ДИСКА:
// файл в 40 КБ не нужно вклеивать в вызов инструмента.
//
//   node scripts/check-pine-file.cjs <путь.pine>
//
// Ловит синтаксис и типы. НЕ ловит рантайм (лимиты drawings, деление на ноль,
// отсутствие volume у символа) — для этого нужен реальный график.

const fs = require('fs');

const file = process.argv[2];
if (!file) {
  console.error('usage: node scripts/check-pine-file.cjs <путь.pine>');
  process.exit(2);
}

const source = fs.readFileSync(file, 'utf8');

(async () => {
  const body = new URLSearchParams();
  body.append('source', source);

  const res = await fetch(
    'https://pine-facade.tradingview.com/pine-facade/translate_light?user_name=Guest&pine_id=00000000-0000-0000-0000-000000000000',
    {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded',
        Referer: 'https://www.tradingview.com/',
      },
      body,
    }
  );

  if (!res.ok) {
    console.error(`TradingView API ${res.status} ${res.statusText}`);
    process.exit(1);
  }

  const json = await res.json();
  const inner = json && json.result;
  const errors = (inner && inner.errors2) || [];
  const warnings = (inner && inner.warnings2) || [];

  console.log(`${file}: ${errors.length ? 'ОШИБКИ' : 'скомпилировано'} — ` +
    `errors ${errors.length}, warnings ${warnings.length}`);

  // errors2 отдаёт шаблон сообщения ({funId} и т.п.) отдельно от подстановок —
  // печатаем запись целиком, иначе текст ошибки нечитаем.
  for (const e of errors) console.log('  E', JSON.stringify(e));
  for (const w of warnings) console.log('  W', JSON.stringify(w));
  if (json.error) console.log('  ERR', json.error);

  // не process.exit(): на Windows выход из async-колбэка при живом fetch-хендле
  // роняет libuv ассертом
  process.exitCode = errors.length ? 1 : 0;
})();
