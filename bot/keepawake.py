"""Не давать Windows уснуть, пока крутится боевой цикл бота.

Зачем: ноут в Modern Standby (S0) засыпает по простою и ЗАМОРАЖИВАЕТ локальный CDP,
TradingView Desktop и опрос облачного буфера — PID жив, но данные не идут. Сигналы
доходят протухшими и режутся гейтом свежести (реально потеряна сделка CRT GER40
23.07.2026: fire 07:00Z, бот увидел 08:16Z, возраст 76 мин > 10 → «Пропущен по
возрасту»). powercfg на AC — первая линия обороны; этот wake-lock — вторая, не
зависящая от схемы питания: пока бот работает, система не уснёт по простою.

Механика: SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) ставит
power-request «системе спать нельзя», действующий до сброса или выхода потока/процесса.
Держится на главном потоке (том, что крутит цикл). Проверить активность:
`powercfg /requests` → раздел SYSTEM покажет python.exe.

No-op на не-Windows и при любой ошибке — power API не должен ронять бота.
"""
from __future__ import annotations

import sys

# winbase.h
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def keep_system_awake(log=None) -> bool:
    """Запретить сон по простою на время жизни вызывающего потока. True — если удалось."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        # Возвращает предыдущее EXECUTION_STATE; NULL(0) = ошибка.
        prev = ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        ok = prev != 0
        if log:
            if ok:
                log.info("Wake-lock: система не уснёт по простою, пока бот работает "
                         "(проверка: powercfg /requests → SYSTEM).")
            else:
                log.warning("Wake-lock: SetThreadExecutionState вернул 0 — не установлен.")
        return ok
    except Exception as e:  # pragma: no cover — зависит от платформы
        if log:
            log.warning("Wake-lock не установлен (%s) — полагаемся на powercfg.", e)
        return False


def release_system_awake() -> None:
    """Снять wake-lock (вернуть обычную политику сна). Вызывать при остановке цикла."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception:
        pass
