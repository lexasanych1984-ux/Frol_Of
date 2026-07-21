#!/usr/bin/env python
"""Точка входа bybit-tradfi-bot.

Команды:
  python run.py check            проверить связь с MT5, показать счёт и символы
  python run.py dryrun-samples   прогнать примеры алертов без MT5 (безопасно)
  python run.py trade            живой цикл (демо; DRY_RUN из .env)
  python run.py stats [дней]     статистика по стратегиям из истории MT5 + CSV
  python run.py report [месяц]   факт демо ↔ коридор бэктеста (деградация эджа);
                                 месяц: YYYY-MM | MM | last | (пусто = текущий);
                                 добавь 'notify' — отправить сводку в Telegram
  python run.py webhook-selftest проверить цепочку облачного буфера (POST /hook →
                                 GET /pull round-trip), MT5 НЕ трогает
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
    # отдельный лог, чтобы примеры НЕ засоряли боевой logs/executions.csv
    dry_log = cfgmod.ROOT / "logs" / "dryrun-executions.csv"
    engine = Engine(cfg, FakeBroker(equity=100000.0), state,
                    exec_log_path=str(dry_log))
    log.info("Прогон %d примеров через парсер + сайзинг (equity=100000 USD, как на демо):", len(SAMPLES))
    for msg in SAMPLES:
        log.info("-" * 60)
        engine.handle_raw(msg)


def cmd_trade(cfg, log):
    from bot.broker_mt5 import MT5Broker
    from bot.health import (HealthMetrics, HealthMonitor, write_pid_file,
                            remove_pid_file)
    from bot.notify import TelegramNotifier

    if cfg.is_live and not cfg.dry_run:
        log.warning("⚠️⚠️ LIVE + ИСПОЛНЕНИЕ: бот будет торговать РЕАЛЬНЫМИ деньгами.")

    notifier = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id,
                                cfg.telegram_prefix)
    mode = ("LIVE РЕАЛЬНЫЕ ДЕНЬГИ" if cfg.is_live else "demo") + \
           (" · DRY_RUN" if cfg.dry_run else " · исполнение")

    # Не удалось даже подключиться к MT5 — это тоже авария, о которой раньше
    # можно было узнать только по молчанию. Сообщаем сразу и падаем.
    broker = MT5Broker(cfg.mt5)
    try:
        broker.connect()
    except Exception as e:
        notifier.send(f"⛔ Бот НЕ запустился: не удалось подключиться к MT5 — {e}. "
                      f"Проверь, что терминал MT5 запущен и залогинен.")
        raise
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
    metrics = HealthMetrics()
    # Порог тревоги «риск выше заданного»: env RISK_OVERSHOOT_PP → expectations →
    # дефолт. Коридоры нужны и движку (порог), и edge-монитору (серия стопов).
    from bot import expectations as expmod
    try:
        exp = expmod.load(cfg.expectations_path())
    except Exception as e:
        log.warning("expectations.yaml не загружен (%s) — тревоги эджа по дефолтам", e)
        exp = None
    overshoot = cfg.report.risk_overshoot_pp
    if overshoot is None:
        overshoot = exp.risk_overshoot_pp if exp else 0.3
    engine = Engine(cfg, broker, state, metrics=metrics, notifier=notifier,
                    exec_log_path=cfg.exec_log_path(), risk_overshoot_pp=overshoot)
    log.info("Лог проскальзывания: %s | тревога о риске при +%.2f п.п. сверх заданного",
             cfg.exec_log_path(), overshoot)

    # Быстрый локальный канал (CDP или loopback-http).
    if cfg.signal_source == "http":
        from bot.signals.http_source import HttpSignalSource
        local_src = HttpSignalSource(cfg.http_host, cfg.http_port, cfg.http_secret)
        log.info("HTTP-приёмник: http://%s:%d (loopback)", cfg.http_host, cfg.http_port)
    else:
        from bot.signals.cdp_source import CdpSignalSource
        local_src = CdpSignalSource(cfg.cdp_port)
        log.info("CDP-чтение алертов TradingView с порта %d", cfg.cdp_port)
        local_src.report_recent_fires()

    # Надёжный резервный канал: pull из облачного store-and-forward буфера.
    # Работает ПАРАЛЛЕЛЬНО, дедуп с локальным каналом — в движке по хэшу.
    webhook_src = None
    if cfg.webhook.enabled:
        from bot.signals.webhook_source import WebhookPullSource
        webhook_src = WebhookPullSource(
            cfg.webhook.pull_url, cfg.webhook.token, state,
            poll_interval_sec=cfg.webhook.poll_interval_sec,
            pull_limit=cfg.webhook.pull_limit,
            request_timeout=cfg.webhook.request_timeout)
        log.info("Webhook-буфер: pull %s каждые %d с (вход max_age=%dм, БУ/выход=%dм)",
                 cfg.webhook.pull_url, cfg.webhook.poll_interval_sec,
                 cfg.freshness.entry_max_age_sec // 60,
                 cfg.freshness.manage_max_age_sec // 60)
    else:
        log.info("Webhook-буфер ВЫКЛЮЧЕН (задай WEBHOOK_PULL_URL/WEBHOOK_TOKEN в .env "
                 "для облачного резерва — иначе сигналы за простой теряются).")

    if webhook_src is not None:
        from bot.signals.composite import CompositeSource
        src = CompositeSource([local_src, webhook_src])
    else:
        src = local_src
    src.start()

    monitor = HealthMonitor(notifier, cfg, broker=broker, source=local_src,
                            metrics=metrics, webhook_source=webhook_src)
    # Живая тревога о серии стопов сверх исторического максимума (второе из двух
    # немедленных событий). Опрашивает историю MT5 раз в edge_check_interval_sec.
    tv_names = {mt5sym: tv for tv, mt5sym in cfg.symbol_map.items()}
    edge = None
    if exp is not None:
        from bot.edgewatch import EdgeMonitor
        edge = EdgeMonitor(notifier, broker, cfg, exp, symbol_alias=tv_names)
        log.info("Edge-мониторинг серии стопов включён (каждые %d мин).",
                 cfg.report.edge_check_interval_sec // 60)

    # Уведомление в Telegram о ЗАКРЫТИИ ботовой позиции (по SL/TP/сигналу) с P&L —
    # следит за историей MT5, а не за exit-алертом (позиции чаще закрывает брокер).
    from bot.closewatch import PositionCloseWatcher
    closes = PositionCloseWatcher(notifier, broker, state, symbol_alias=tv_names)
    log.info("Уведомления о закрытии сделок включены (опрос каждые %d с).",
             closes.interval)
    pidf = write_pid_file(cfg)
    log.info("pid-файл для внешнего watchdog: %s", pidf)
    monitor.hello(mode, login=acc.login)
    # Немедленная первая проверка: не ждать 5 минут, чтобы поймать уже
    # существующую аварию (напр. алго-торговля выключена на старте).
    monitor.start_first_check()

    # Опрос с таймаутом (а не блокирующий stream): цикл регулярно просыпается и
    # запускает проверки живости, даже когда сигналов нет. Короткий таймаут ещё
    # и держит Ctrl+C отзывчивым на Windows.
    poll_timeout = max(1.0, min(cfg.health.check_interval_sec, 5.0))
    log.info("Ожидание сигналов... (Ctrl+C для выхода). Мониторинг живости включён.")
    try:
        while True:
            rs = src.poll(poll_timeout)
            if rs is not None:
                try:
                    engine.handle_raw(rs.raw, received_ts=rs.received_ts,
                                      source=rs.source, ext_id=rs.ext_id)
                except Exception as e:  # один плохой сигнал не должен ронять бота
                    log.exception("Ошибка обработки сигнала: %s", e)
            monitor.maybe_tick()
            if edge is not None:
                edge.maybe_tick()
            closes.maybe_tick()
    except KeyboardInterrupt:
        log.info("Останов по Ctrl+C")
    finally:
        monitor.goodbye()
        remove_pid_file(cfg)
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
        from bot import execlog
        exec_index = execlog.index_by_position(cfg.exec_log_path())
        st.write_csv(trades, csv_path, start_balance=start_balance,
                     exec_index=exec_index)
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


def _parse_report_args(argv):
    """Из хвоста argv достать (year, month, notify). Токены: YYYY-MM | MM | last |
    notify/tg/telegram. Пусто → текущий месяц, без отправки."""
    from datetime import datetime
    notify = False
    year = month = None
    for tok in argv:
        t = tok.strip().lower()
        if t in ("notify", "tg", "telegram", "--notify"):
            notify = True
        elif t in ("last", "прошлый", "prev"):
            d = datetime.now().replace(day=1)
            prev = (d.month - 2) % 12 + 1
            yr = d.year - 1 if d.month == 1 else d.year
            year, month = yr, prev
        elif "-" in t:                       # YYYY-MM
            try:
                y, m = t.split("-")[:2]
                year, month = int(y), int(m)
            except ValueError:
                pass
        elif t.isdigit():                    # MM
            month = int(t)
    now = datetime.now()
    if month is None:
        year, month = now.year, now.month
    if year is None:
        year = now.year
    return year, month, notify


def cmd_report(cfg, log):
    """Факт демо ↔ коридор бэктеста: пометки OK/ВНИМАНИЕ/ПРОБЛЕМА, слиппедж,
    честность выборки. Пишет Markdown в logs/reports/, опц. сводку в Telegram."""
    from datetime import datetime

    from bot import execlog, expectations as expmod, report as rep, stats as st
    from bot.broker_mt5 import MT5Broker
    from bot.notify import TelegramNotifier

    year, month, notify = _parse_report_args(sys.argv[2:])
    month_label = f"{year:04d}-{month:02d}"

    exp = expmod.load(cfg.expectations_path())
    broker = MT5Broker(cfg.mt5)
    broker.connect()
    tv_names = {mt5sym: tv for tv, mt5sym in cfg.symbol_map.items()}
    trades = st.collect_closed(broker.mt5, days=cfg.report.history_days,
                               symbol_alias=tv_names)
    acc = broker.account()
    broker.shutdown()

    exec_index = execlog.index_by_position(cfg.exec_log_path())
    exec_rows = execlog.read(cfg.exec_log_path())
    ref_equity = acc.balance or acc.equity

    # По каждой стратегии из коридоров — накопительный факт демо vs коридор.
    per_strategy = []
    for key, se in exp.by_key.items():
        label = se.label
        label_trades = [t for t in trades if t.strategy == label]
        per_strategy.append(rep.build_strategy_report(
            label, label_trades, exec_index, se, exp.min_sample, ref_equity))

    month_trades = [t for t in trades
                    if t.close_time.year == year and t.close_time.month == month]
    slip = rep.slippage_summary(exec_rows)      # накопительно (слиппедж системен)

    now = datetime.now()
    md = rep.render_markdown(month_label, per_strategy, slip, now,
                             month_trades, exp.min_sample)
    reports_dir = cfg.reports_dir_path()
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"report-{month_label}.md"
    out.write_text(md, encoding="utf-8")

    # Консольная выжимка
    log.info("Отчёт эджа за %s (история %d дн., n=%d сделок демо):",
             month_label, cfg.report.history_days, len(trades))
    for r in per_strategy:
        if r.n == 0:
            log.info("  %-5s: нет сделок", r.label)
            continue
        tag = "⏳ мало данных" if not r.enough else r.worst_verdict()
        wr = f"{r.win_rate*100:.0f}%" if r.win_rate is not None else "—"
        pf = ("∞" if r.profit_factor == "∞"
              else (f"{r.profit_factor:.2f}" if r.profit_factor is not None else "—"))
        log.info("  %-5s: n=%-3d WR=%-4s PF=%-5s net=%-8.0f W/BE/L=%d/%d/%d  [%s]",
                 r.label, r.n, wr, pf, r.net, r.wins, r.be, r.stops, tag)
    log.info("Markdown-отчёт: %s", out)

    if notify:
        notifier = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id,
                                    cfg.telegram_prefix)
        text = rep.render_telegram(month_label, per_strategy, slip, exp.min_sample)
        ok = notifier.send(text)
        log.info("Telegram-сводка: %s", "отправлена" if ok else "НЕ отправлена")


def cmd_webhook_selftest(cfg, log):
    """Round-trip облачного буфера: POST тестового тела в /hook → GET /pull.

    Проверяет всю цепочку приёмника (TLS, токен, запись, выдача) без MT5 и без
    движка. Тело намеренно НЕ является торговым сигналом (не распарсится как ордер).
    """
    import time as _t

    import requests

    if not cfg.webhook.enabled:
        log.error("Webhook-буфер не настроен: задай WEBHOOK_PULL_URL и WEBHOOK_TOKEN "
                  "в .env (см. cloud/README.md).")
        sys.exit(2)
    base, token = cfg.webhook.pull_url, cfg.webhook.token
    to = cfg.webhook.request_timeout
    body = f"SELFTEST {int(_t.time())} — проверка цепочки webhook (не сигнал)"

    log.info("Webhook-selftest → %s", base)
    try:
        head = requests.get(f"{base}/head/{token}", timeout=to)
        head.raise_for_status()
        after = int((head.json() or {}).get("max_id") or 0)
        log.info("  /head ok: max_id=%d", after)
    except Exception as e:
        log.error("  /head ПРОВАЛ: %s (проверь URL/токен/сеть)", e)
        sys.exit(1)

    t0 = _t.time()
    try:
        post = requests.post(f"{base}/hook/{token}", data=body.encode("utf-8"),
                             headers={"Content-Type": "text/plain; charset=utf-8"},
                             timeout=to)
        log.info("  POST /hook → %s (%.0f мс)", post.status_code,
                 (_t.time() - t0) * 1000)
        if post.status_code != 200:
            log.error("  приёмник ответил не 200: %s", post.text[:200])
            sys.exit(1)
    except Exception as e:
        log.error("  POST /hook ПРОВАЛ: %s", e)
        sys.exit(1)

    deadline = _t.time() + 15
    while _t.time() < deadline:
        try:
            pull = requests.get(f"{base}/pull/{token}",
                                params={"after": after, "limit": 50}, timeout=to)
            pull.raise_for_status()
            recs = pull.json() or []
        except Exception as e:
            log.error("  GET /pull ПРОВАЛ: %s", e)
            sys.exit(1)
        hit = next((r for r in recs if r.get("body") == body), None)
        if hit:
            log.info("  ✅ round-trip ok: запись id=%s вернулась через /pull "
                     "(%.0f мс полный цикл).", hit.get("id"), (_t.time() - t0) * 1000)
            log.info("Цепочка облачного буфера РАБОТАЕТ. Дедуп-повтор POST того же "
                     "тела не создаст вторую запись (проверка в cloud/README.md).")
            return
        _t.sleep(1)
    log.error("  ❌ отправленная запись не вернулась через /pull за 15 с — "
              "проверь Worker (логи wrangler tail) и схему D1.")
    sys.exit(1)


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
    elif cmd == "report":
        cmd_report(cfg, log)
    elif cmd == "webhook-selftest":
        cmd_webhook_selftest(cfg, log)
    else:
        log.error("Неизвестная команда %r. Доступно: check | dryrun-samples | "
                  "trade | stats | report | webhook-selftest", cmd)
        sys.exit(2)


if __name__ == "__main__":
    main()
