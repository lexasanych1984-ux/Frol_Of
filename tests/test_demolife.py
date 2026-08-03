"""Напоминание о переезде с демо-счёта: пороги и формулировки."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import demolife

OPENED = date(2026, 8, 3)  # счёт 246113


def _at(day: int):
    """Статус на N-й день жизни счёта (день открытия = 1)."""
    return demolife.status(OPENED, date.fromordinal(OPENED.toordinal() + day - 1))


def test_day_one_is_quiet():
    st = _at(1)
    assert st.day == 1 and st.days_left == 14
    assert st.level == demolife.OK
    assert not st.should_notify


def test_quiet_until_day_ten():
    for day in range(1, 10):
        assert _at(day).level == demolife.OK, day


def test_warns_from_day_ten():
    st = _at(10)
    assert st.level == demolife.WARN
    assert st.should_notify
    assert st.days_left == 5


def test_urgent_from_day_twelve():
    st = _at(12)
    assert st.level == demolife.URGENT
    assert "ДЕМО УМРЁТ" in st.text  # капсом, как просили
    assert st.days_left == 3


def test_expired_after_lifetime():
    st = _at(15)
    assert st.level == demolife.EXPIRED
    assert st.days_left <= 0
    assert st.should_notify


def test_thresholds_are_configurable():
    st = demolife.status(OPENED, date(2026, 8, 8), warn_from_day=3, urgent_from_day=5)
    assert st.level == demolife.URGENT


def test_bad_date_disables_reminder():
    from bot.config import DemoAccountCfg
    assert DemoAccountCfg(opened="не дата").opened_date() is None
    assert DemoAccountCfg(opened="").opened_date() is None
    assert DemoAccountCfg(opened="2026-08-03").opened_date() == OPENED
