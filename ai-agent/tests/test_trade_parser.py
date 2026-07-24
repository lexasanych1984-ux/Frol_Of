"""Тесты парсера /trade (src/trade_log/parser.py) на реальных формулировках
трейдера. Ключевое: числа с запятой-разделителем, сделка без тейка, англ./рус.
ключи и неоднозначный сетап (карточка должна попросить уточнить, а не молча
выбрать один вариант Entry)."""
from __future__ import annotations

from src.trade_log.parser import body_note, format_card, parse_trade_text


def test_full_short_ger40_with_setup():
    p = parse_trade_text("шорт GER40, вход 24100 стоп 24150 тейк 24000, сетап 1Н поглощение")
    assert p.direction == "Short"
    assert p.symbol == "GER40"
    assert p.entry == 24100
    assert p.stop == 24150
    assert p.take == 24000
    assert p.entry_label == "1Н поглощение манипуляции"
    assert p.planned_rr == 2.0
    assert p.is_complete


def test_long_eurusd_comma_decimals():
    """Десятичная запятая (1,0850) — не должна ломать разбор чисел."""
    p = parse_trade_text("лонг EURUSD вход 1,0850 стоп 1,0820 тейк 1,0920 сетап FVG 1H")
    assert p.direction == "Long"
    assert p.symbol == "EURUSD"
    assert p.entry == 1.0850
    assert p.stop == 1.0820
    assert p.take == 1.0920
    assert p.entry_label == "FVG 1H"


def test_english_keywords_entry_without_keyword():
    """Англ. ключи sl/tp и вход без слова «вход» — первое число после
    инструмента (buy usdcad 1.3610 ...) должно стать входом."""
    p = parse_trade_text("buy usdcad 1.3610 sl 1.3580 tp 1.3700 qm+ob")
    assert p.direction == "Long"
    assert p.symbol == "USDCAD"
    assert p.entry == 1.3610
    assert p.stop == 1.3580
    assert p.take == 1.3700
    assert p.entry_label == "QM+OB"


def test_no_take_is_optional():
    """Сделка без тейка: take=None, но карточка полная (тейк не обязателен),
    план RR посчитать нельзя."""
    p = parse_trade_text("шорт GER40 вход 24100 стоп 24150 сетап 1Н поглощение")
    assert p.symbol == "GER40"
    assert p.entry == 24100
    assert p.stop == 24150
    assert p.take is None
    assert p.entry_label == "1Н поглощение манипуляции"
    assert p.planned_rr is None
    assert p.is_complete  # тейк опционален


def test_prodazha_5m_variant():
    """«продажа» -> Short; «5m поглощение» -> именно 5m-вариант Entry (не 1Н)."""
    p = parse_trade_text("продажа GBPUSD вход 1.2650 стоп 1.2680 тейк 1.2560 5m поглощение")
    assert p.direction == "Short"
    assert p.symbol == "GBPUSD"
    assert p.entry == 1.2650
    assert p.stop == 1.2680
    assert p.take == 1.2560
    assert p.entry_label == "5m поглощение манипуляции"


def test_ambiguous_setup_asks_to_clarify():
    """«поглощение манипуляции» подстрокой входит и в 1Н, и в 5m вариант Entry —
    парсер не должен молча выбирать один: entry_label пуст, оба в ambiguous."""
    p = parse_trade_text("лонг EURUSD вход 1.0850 стоп 1.0820 поглощение манипуляции")
    assert p.entry_label is None
    assert set(p.ambiguous_setups) == {"1Н поглощение манипуляции", "5m поглощение манипуляции"}
    assert not p.is_complete
    card = format_card(p)
    assert "уточни" in card.lower()
    assert "1Н поглощение манипуляции" in card and "5m поглощение манипуляции" in card


def test_thousands_space_separator():
    """Пробел как разделитель тысяч (24 100) — одно число, а не два."""
    p = parse_trade_text("шорт GER40 вход 24 100 стоп 24 150 сетап 1Н поглощение")
    assert p.entry == 24100
    assert p.stop == 24150


def test_missing_fields_reported():
    p = parse_trade_text("взял EURUSD по рынку, сетап QM+OB")
    assert p.symbol == "EURUSD"
    assert p.entry_label == "QM+OB"
    assert "цена входа" in p.missing
    assert "стоп" in p.missing
    assert "направление (лонг/шорт)" in p.missing
    assert not p.is_complete


def test_body_note_contains_prices_and_rr():
    p = parse_trade_text("шорт GER40, вход 24100 стоп 24150 тейк 24000, сетап 1Н поглощение")
    note = body_note(p)
    assert "Вход 24100" in note
    assert "Стоп 24150" in note
    assert "Тейк 24000" in note
    assert "План RR 2" in note


def test_card_preserves_trailing_zeros():
    """Хвостовые нули в ценах не должны теряться при показе (1,0850 -> 1.0850,
    а не 1.085 после float)."""
    p = parse_trade_text("лонг EURUSD вход 1,0850 стоп 1,0820 тейк 1,0920 сетап FVG 1H")
    card = format_card(p)
    assert "Вход: 1.0850" in card
    assert "Стоп: 1.0820" in card
    assert "Тейк: 1.0920" in card
    assert "Вход 1.0850" in body_note(p)
