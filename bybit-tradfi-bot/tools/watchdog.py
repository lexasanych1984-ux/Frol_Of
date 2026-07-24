#!/usr/bin/env python
"""Внешний чекер живости процесса бота — запускается Планировщиком раз в час.

Ловит случай, который встроенный health поймать не может: бот УПАЛ целиком
(вместе со своим мониторингом). Тогда встроенные проверки молчат — а молчание
это авария. Этот скрипт живёт отдельным процессом и смотрит только одно: жив ли
процесс бота.

Логика:
  • pid-файл есть и процесс с этим PID жив (python) → всё ок, молчим;
  • pid-файл есть, а процесс мёртв → бот УПАЛ аварийно → Telegram-тревога;
  • pid-файла нет → бот остановлен штатно (он сам удаляет pid-файл и шлёт «⏹»)
    или ещё не запускался → молчим, чтобы не долбить после плановой остановки.

Анти-нагбек: тревога об одном и том же падении шлётся не чаще
WATCHDOG_MIN_INTERVAL_MIN минут (по умолчанию 55 — при часовом расписании
каждая проверка всё равно проходит).

Регистрация задачи: tools/install-watchdog-task.ps1
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Пакет bot лежит на уровень выше tools/
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import config as cfgmod          # noqa: E402
from bot.health import pid_path           # noqa: E402
from bot.notify import send_telegram      # noqa: E402

LOG = ROOT / "logs" / "watchdog.log"


def _log(msg: str) -> None:
    """Пишем строку в logs\\watchdog.log сами (не через redirect в .bat).

    Файл — UTF-8 с BOM в начале, чтобы `Get-Content logs\\watchdog.log` в Windows
    PowerShell 5.1 БЕЗ -Encoding читал кириллицу правильно, а не как cp1251.
    Консольный вывод — best-effort (под Планировщиком консоли нет).
    """
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        fresh = (not LOG.exists()) or LOG.stat().st_size == 0
        with open(LOG, "a", encoding="utf-8") as f:
            if fresh:
                f.write("\ufeff")   # BOM только в самом начале файла
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line)
    except Exception:
        pass


def _process_alive(pid: int, name_hint: str = "python") -> bool:
    """Жив ли процесс с данным PID и это действительно python-процесс.

    Проверка имени образа страхует от переиспользования PID: после падения бота
    его номер может занять посторонний процесс.
    """
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        # Не смогли опросить — не выдумываем падение (лучше молчать, чем ложная
        # тревога). Следующий час опросим снова.
        return True
    line = (out.stdout or "").strip()
    if not line or "No tasks" in line or str(pid) not in line:
        return False
    image = line.split(",", 1)[0].strip().strip('"').lower()
    return name_hint in image


def _read_pid(p: Path):
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def _state_file(pidf: Path) -> Path:
    return pidf.parent / "watchdog.state"


def _load_state(sf: Path) -> dict:
    try:
        return json.loads(sf.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(sf: Path, data: dict) -> None:
    try:
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps(data), encoding="utf-8")
    except Exception as e:
        _log(f"watchdog: не удалось сохранить состояние: {e}")


def main() -> int:
    cfg = cfgmod.load()
    pidf = pid_path(cfg)
    sf = _state_file(pidf)
    state = _load_state(sf)
    now = time.time()
    min_interval = int(os.getenv("WATCHDOG_MIN_INTERVAL_MIN", "55")) * 60

    pid = _read_pid(pidf)

    if pid is None:
        # Штатная остановка или ещё не запускался — бот сам всё сообщает.
        _log("watchdog: pid-файла нет — бот остановлен штатно/не запущен, молчу.")
        _save_state(sf, {"last_state": "stopped"})
        return 0

    if _process_alive(pid):
        _log(f"watchdog: процесс бота жив (pid={pid}).")
        _save_state(sf, {"last_state": "alive"})
        return 0

    # pid-файл есть, а процесс мёртв → аварийное падение.
    last_alert = float(state.get("last_alert_ts", 0) or 0)
    if now - last_alert < min_interval:
        _log(f"watchdog: падение обнаружено (pid={pid}), но тревога недавно "
             f"уже уходила — молчу (анти-нагбек).")
        return 0

    prefix = cfg.telegram_prefix or ""
    text = (f"{prefix} 🛑 ПРОЦЕСС БОТА НЕ ЗАПУЩЕН (аварийное падение?).\n"
            f"pid-файл есть (pid={pid}), но процесс не найден — встроенный "
            f"мониторинг молчит вместе с ботом.\n"
            f"Что делать: запусти start-bot.bat (или start-bot.ps1) заново.").strip()
    sent = send_telegram(cfg.telegram_token, cfg.telegram_chat_id, text)
    _log(f"watchdog: обнаружено падение бота (pid={pid}); "
         f"Telegram отправлен={sent}.")
    _save_state(sf, {"last_state": "dead", "last_alert_ts": now})
    return 1


if __name__ == "__main__":
    sys.exit(main())
