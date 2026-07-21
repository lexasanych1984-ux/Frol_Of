"""Живость источника сигналов: CDP-поток «получал кадры за последние N минут».

Отдельный файл, чтобы зависимость websocket-client не тянулась в тесты машины
переходов (test_health.py).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.signals.cdp_source import CdpSignalSource


def test_cdp_health_not_connected_is_fail():
    s = CdpSignalSource(port=9222)
    ok, detail, remedy = s.health(now=1000, stale_sec=600)
    assert ok is False
    assert "не установлено" in detail
    assert remedy  # есть подсказка «что делать»


def test_cdp_health_startup_grace_then_fail():
    # На старте соединение ещё устанавливается — не паникуем, но после форы
    # молчание = авария.
    s = CdpSignalSource()
    s._started_at = 1000.0
    s._connect_grace = 45.0
    ok, _, _ = s.health(now=1010.0, stale_sec=600)     # в пределах форы
    assert ok is True
    ok, detail, _ = s.health(now=1060.0, stale_sec=600)  # фора прошла
    assert ok is False and "не установлено" in detail


def test_cdp_health_fresh_frame_is_ok():
    s = CdpSignalSource()
    s._connected = True
    s._connected_since = 1000
    s._last_frame_ts = 1000
    ok, _, _ = s.health(now=1300, stale_sec=600)   # кадр 5 мин назад
    assert ok is True


def test_cdp_health_stale_frame_is_fail():
    s = CdpSignalSource()
    s._connected = True
    s._connected_since = 1000
    s._last_frame_ts = 1000
    ok, detail, _ = s.health(now=2000, stale_sec=600)  # кадров нет ~16 мин
    assert ok is False
    assert "завис" in detail


def test_cdp_health_connected_but_no_frames_grace_then_fail():
    s = CdpSignalSource()
    s._connected = True
    s._connected_since = 1000
    s._last_frame_ts = 0                 # кадров ещё не было
    ok, _, _ = s.health(now=1300, stale_sec=600)       # в пределах ожидания
    assert ok is True
    ok, detail, _ = s.health(now=2000, stale_sec=600)  # ждём слишком долго
    assert ok is False
    assert "ни одного" in detail


def test_http_health_reports_down_when_not_started():
    from bot.signals.http_source import HttpSignalSource
    s = HttpSignalSource()
    ok, detail, remedy = s.health(now=0, stale_sec=600)
    assert ok is False
    assert remedy
