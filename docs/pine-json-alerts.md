# Перевод alert_message на JSON (рекомендуется для реальных денег)

Текстовый формат бот понимает, но JSON надёжнее: явные поля, явный `id` для
идемпотентности, и на каждом событии (вход/выход/БУ) несёт актуальные SL/TP.

Меняется **только строка `alert_message`** внутри `strategy.entry/exit` — логика
стратегии не трогается. Ниже — замены для каждого места.

## Вспомогательная функция (добавь один раз в начало стратегии)
```pine
f_json(_action, _side, _kind, _entry, _sl, _tp, _rr) =>
    '{"action":"' + _action + '","side":"' + _side + '","symbol":"' + syminfo.ticker +
      '","order_kind":"' + _kind + '","entry":' + str.tostring(_entry) +
      ',"sl":' + str.tostring(_sl) + ',"tp":' + str.tostring(_tp) +
      ',"rr":' + str.tostring(_rr) +
      ',"id":"' + str.tostring(time) + '_' + syminfo.ticker + '_' + _side + '"}'
```

## SMC — вход (замена в текущих строках 588 / 684)
```pine
// LONG (market)
strategy.entry("Long", strategy.long, qty=qty,
     alert_message = f_json("entry", "long", "market", close, stopP, tpP, (tpP-close)/riskD))
// SHORT (market)
strategy.entry("Short", strategy.short, qty=qtyS,
     alert_message = f_json("entry", "short", "market", close, stopS, tpPS, (close-tpPS)/riskDS))
```

## SMC — выход и безубыток
```pine
strategy.exit("Long TP/SL", "Long", stop=stopP, limit=tpP,
     alert_message = f_json("exit", "long", "market", close, stopP, tpP, na))
// перенос в БУ:
strategy.exit("Long TP/SL", "Long", stop=tEntry, limit=tTP,
     alert_message = f_json("be", "long", "market", tEntry, tEntry, tTP, na))
```

## CRT — вход (limit / stop)
```pine
// limit-версия (строки 685/688): order_kind = "limit", entry = entryLvl
strategy.entry("CRT L", strategy.long, qty=qtyL, limit=entryLvl,
     alert_message = f_json("entry", "long", "limit", entryLvl, curSL, curTP, rrMult))
// stop-версия (строки 700/703): order_kind = "stop"
strategy.entry("CRT L", strategy.long, qty=qty, stop=entryLvl,
     alert_message = f_json("entry", "long", "stop", entryLvl, curSL, curTP, rrMult))
```

## Asia Sweep — вход (строки 542/566)
```pine
strategy.entry("L", strategy.long, qty=qty2,
     alert_message = f_json("entry", "long", "market", close, slP2, tpP2, rewD2/riskD2))
```

## После правки
1. `pine_save` стратегии.
2. Пересоздай strategy-алерт (message остаётся `{{strategy.order.alert_message}}`).
3. В `.env` можно оставить любой `SIGNAL_SOURCE` — парсер сам определит JSON по `{`.
