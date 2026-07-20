#!/usr/bin/env python
"""Точка входа bybit-tradfi-bot.

Команды:
  python run.py check            проверить связь с MT5, показать счёт и символы
  python run.py dryrun-samples   прогнать примеры алертов без MT5 (безопасно)
  python run.py trade            живой цикл (демо; DRY_RUN из .env)
  python run.py stats [дней]     статистика по стратегиям из истории MT5 + CSV
"""
from __future__ import annotations

import sys

from bot import config as cfgmod
from bot import logutil
from bot.engine import Engine
from bot.state import State

SAMPLES = [
    "LONG EURUSD | вход 1.14355 | SL 1.14100 | TP 1.14900 | RR 2.00",
    "SHORT NAS100 | вход 20100 | SL 20180 | TP 19900 | RR 2.5",
    "CRT SHORT (лимит) GER40 | вход 24500 | SL 24620 | TP 24100 | RR 3.0",
    "CRT LONG GER40 | вход 24500 | SL 24400 | TP 24800 | RR 3.0",
    "ВЫХОД long (после БУ) EURUSD",
    "ВЫХОД short NAS100",
    '{"action":"entry","side":"long","symbol":"EURUSD","order_kind":"market",'
    '"entry":1.1435,"sl":1.1410,"tp":1.1490,"rr":2.0,"strategy":"smc","id":"abc123"}',
]


def _banner(cfg: cfgmod.Config, log):
    mode = "LIVE ⚠️ РЕАЛЬНЫЕ ДЕНЬГИ" if cfg.is_live else "DEMO (виртуальные)"
    dry = "DRY_RUN (только лог)" if cfg.dry_run else "ИСПОЛНЕНИЕ ВКЛЮЧЕНО"
    log.info("=" * 60)
    log.info("bybit-tradfi-bot | среда: %s | режим: %s", mode, dry)
    log.info("источник сигналов: %s", cfg.signal_source)
    log.info("=" * 60)


def cmd_check(cfg, log):
    from bot.broker_mt5 import MT5Broker
    broker = MT5Broker(cfg.mt5)
    broker.connect()
    acc = broker.account()
    log.info("Счёт %s @ %s | equity=%.2f %s | balance=%.2f",
             acc.login, acc.server, acc.equity, acc.currency, acc.balance)
    for tv, mt5sym in cfg.symbol_map.items():
        spec = broker.symbol_spec(mt5sym)
        if spec:
            log.info("  %s → %s | min=%s step=%s tick_size=%s tick_value=%s",
                     tv, mt5sym, spec.volume_min, spec.volume_step,
                     spec.tick_size, spec.tick_value)
        else:
            log.warning("  %s → %s: символ НЕ найден в MT5 (проверь имя)", tv, mt5sym)
    broker.shutdown()


def cmd_dryrun_samples(cfg, log):
    from bot.fakebroker import FakeBroker
    # принудительный dry-run и demo для безопасности
    cfg.dry_run = True
    cfg.env = "demo"
    state = State(":memory:")
    # уникальные ключи, чтобы дедуп не глотал разные примеры
    state.is_duplicate = lambda *a, **k: False  # type: ignore
    engine = Engine(cfg, FakeBroker(equity=100000.0), state)
    log.info("Прогон %d примеров через парсер + сайзинг (equity=100000 USD, как на демо):", len(SAMPLES))
    for msg in SAMPLES:
        log.info("-" * 60)
        engine.handle_raw(msg)


def cmd_trade(cfg, log):
    from bot.broker_mt5 import MT5Broker
    if cfg.is_live and not cfg.dry_run:
        log.warning("⚠️⚠️ LIVE + ИСПОЛНЕНИЕ: бот будет торговать РЕАЛЬНЫМИ деньгами.")
    broker = MT5Broker(cfg.mt5)
    broker.connect()
    acc = broker.account()
    log.info("Подключено: счёт %s equity=%.2f %s", acc.login, acc.equity, acc.currency)

    # Без этой кнопки терминал молча рубит КАЖДЫЙ ордер (retcode 10027),
    # а сигнал восстановить нельзя — предупреждаем громко и заранее.
    if not cfg.dry_run and broker.autotrading_enabled() is False:
        log.warning("!" * 60)
        log.warning("⚠️  %s", broker.AUTOTRADING_HINT)
        log.warning("!" * 60)

    state = State()
    state.prune()
    engine = Engine(cfg, broker, state)

    if cfg.signal_source == "http":
        from bot.signals.http_source import HttpSignalSource
        src = HttpSignalSource(cfg.http_host, cfg.http_port, cfg.http_secret)
        log.info("HTTP-приёмник: http://%s:%d (loopback)", cfg.http_host, cfg.http_port)
    else:
        from bot.signals.cdp_source import CdpSignalSource
        src = CdpSignalSource(cfg.cdp_port)
        log.info("CDP-чтение алертов TradingView с порта %d", cfg.cdp_port)
        src.report_recent_fires()

    src.start()
    log.info("Ожидание сигналов... (Ctrl+C для выхода)")
    try:
        for raw in src.stream():
            try:
                engine.handle_raw(raw)
            except Exception as e:  # один плохой сигнал не должен ронять бота
                log.exception("Ошибка обработки сигнала: %s", e)
    except KeyboardInterrupt:
        log.info("Останов по Ctrl+C")
    finally:
        src.stop()
        broker.shutdown()
        state.close()


def cmd_stats(cfg, log):
    """Статистика по закрытым сделкам из истории MT5, раздельно по стратегиям."""
    from bot import stats as st
    from bot.broker_mt5 import MT5Broker
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 365
    broker = MT5Broker(cfg.mt5)
    broker.connect()
    # MT5-имена мини-контрактов → тикеры TradingView (GER30m → GER40 и т.д.),
    # чтобы отчёт и Notion-база говорили на языке стратегий.
    tv_names = {mt5sym: tv for tv, mt5sym in cfg.symbol_map.items()}
    trades = st.collect_closed(broker.mt5, days=days, symbol_alias=tv_names)
    if not trades:
        log.info("Закрытых сделок за %d дн. пока нет.", days)
    else:
        log.info("Закрытых сделок за %d дн.: %d", days, len(trades))
        log.info("-" * 78)
        for row in st.summarize(trades):
            log.info("  %-14s сделок=%-4s winrate=%-5s net=%-10s PF=%-5s "
                     "ср+=%-8s ср-=%-8s макс-=%s",
                     row["стратегия"], row["сделок"], row["winrate"], row["net_pnl"],
                     row["profit_factor"], row["ср.плюс"], row["ср.минус"], row["макс.минус"])
        # стартовый баланс = текущий минус P&L всех закрытых сделок
        acc = broker.account()
        start_balance = acc.balance - sum(t.profit for t in trades)
        csv_path = cfgmod.ROOT / "logs" / "trades.csv"
        st.write_csv(trades, csv_path, start_balance=start_balance)
        log.info("-" * 78)
        log.info("Полный список сделок: %s (открывается в Excel)", csv_path)
    opened = st.open_positions(broker.mt5)
    if opened:
        log.info("Открытых позиций сейчас: %d", len(opened))
        for p in opened:
            log.info("  %(символ)s %(сторона)s %(лот)s лот [%(стратегия)s] "
                     "вход=%(вход)s SL=%(SL)s TP=%(TP)s | плавающий P&L=%(плавающий P&L)s", p)
    else:
        log.info("Открытых позиций сейчас нет.")
    broker.shutdown()


def main():
    cfg = cfgmod.load()
    log = logutil.setup(cfg.logging_cfg.get("level", "INFO"),
                        cfg.logging_cfg.get("file", "logs/bot.log"))
    _banner(cfg, log)

    cmd = sys.argv[1] if len(sys.argv) > 1 else "dryrun-samples"
    if cmd == "check":
        cmd_check(cfg, log)
    elif cmd == "dryrun-samples":
        cmd_dryrun_samples(cfg, log)
    elif cmd == "trade":
        cmd_trade(cfg, log)
    elif cmd == "stats":
        cmd_stats(cfg, log)
    else:
        log.error("Неизвестная команда %r. Доступно: check | dryrun-samples | trade | stats", cmd)
        sys.exit(2)


if __name__ == "__main__":
    main()
