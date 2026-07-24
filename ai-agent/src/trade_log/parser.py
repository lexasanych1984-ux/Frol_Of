"""Детерминированный разбор быстрого текстового лога сделки для команды /trade.

Трейдер пишет одной строкой, например:
    «шорт GER40, вход 24100 стоп 24150 тейк 24000, сетап 1Н поглощение»
Задача — вытащить направление, инструмент, вход/стоп/тейк и НАЗВАНИЕ СЕТАПА,
которое ложится в select-поле Entry журнала Notion (ключевая цель — заполнить
Entry, чтобы /setup-статистика перестала быть слепой).

Разбор нарочно БЕЗ LLM: поля структурны, а словарь Entry закрыт и мал —
регэкспы + фаззи-матч по словарю (переиспользуем setup_check.find_matching_labels)
предсказуемее и покрываются юнит-тестами (числа с запятыми, сделка без тейка,
англ./рус. ключи, неоднозначный сетап). Голосовой ввод (STT) — отдельный
будущий шаг; сюда придёт уже распознанный текст.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from src.matching.matcher import normalize_symbol
from src.notion_sync import schema
from src.setup_check.finder import find_matching_labels

# Инструменты трейдера (совпадают со справочником Pairs в Notion). Символ в тексте
# сверяется с этим списком через normalize_symbol, чтобы "usdcad" == "USDCAD",
# "GER40," == "GER40". Реальный резолв в page_id при создании строки идёт по
# ЖИВОМУ справочнику Pairs (journal_writer) — здесь список только для распознавания.
DEFAULT_SYMBOLS = [
    "GER40", "US30", "US100", "US500", "DXY",
    "EURUSD", "GBPUSD", "USDCAD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD",
    "GBPJPY", "XAUUSD", "XAGUSD", "USOIL",
    "BTCUSDT", "ETHUSDT",
]

# Направление: MT5/журнал оперируют Long/Short (справочник Direction в Notion).
_SHORT_WORDS = ["шорт", "short", "sell", "селл", "продажа", "продаж", "продам", "продай"]
_LONG_WORDS = ["лонг", "long", "buy", "бай", "покупка", "покупк", "куплю", "купил"]

# Пробелы, которыми могут разделять тысячи: обычный и неразрывный (  —
# частый гость из копипаста / телефонной клавиатуры).
_SPACES = "  "  # обычный пробел + неразрывный (частый гость из копипаста)

# Число: либо сгруппированное по тысячам пробелом (24 100), либо обычное с
# десятичной точкой/запятой (1.0850 / 1,0850 / 24100). Группа тысяч — строго по
# 3 цифры, иначе regex «склеил» бы два соседних числа "1.3610 1.3580" в одно.
_NUM = r"\d{1,3}(?:[" + _SPACES + r"]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"

# Ключи цен. Длинные варианты стоят ПЕРВЫМИ в альтернации: для "стоп-лосс 1.35"
# движок должен съесть всё слово до числа, иначе "стоп" совпадёт, а "[-]лосс"
# останется между ключом и числом и число не найдётся.
_ENTRY_KEY = r"вход|entry|войти|вош[её]л"
_STOP_KEY = r"стоп[-\s]?лосс|стоплосс|стоп|\bsl\b|\bstop\b"
_TAKE_KEY = r"тейк[-\s]?профит|тейкпрофит|тейк|\btp\b|\btake\b"
_SETUP_MARKER = r"сет[аеэ]п|setup"

_SEP = r"[\s:=\-]*"  # разделители между ключом и числом (пробел, ":", "=", "-")


def _clean_raw(raw: str) -> str:
    """Нормализует написание числа для показа, СОХРАНЯЯ хвостовые нули:
    '24 100'->'24100', '1,0850'->'1.0850' (float потерял бы 0 на конце)."""
    for sp in _SPACES:
        raw = raw.replace(sp, "")
    return raw.replace(",", ".")


def _to_float(raw: str) -> float:
    return float(_clean_raw(raw))


def _num_after(text: str, key: str) -> Optional[tuple[float, str]]:
    """Возвращает (число, как-написано) после ключа, либо None."""
    m = re.search(rf"(?:{key}){_SEP}({_NUM})", text, flags=re.IGNORECASE)
    if not m:
        return None
    return _to_float(m.group(1)), _clean_raw(m.group(1))


def _find_direction(text: str) -> Optional[str]:
    low = text.lower()
    for w in _SHORT_WORDS:
        if re.search(rf"\b{re.escape(w)}", low):
            return schema.DIRECTION_SHORT
    for w in _LONG_WORDS:
        if re.search(rf"\b{re.escape(w)}", low):
            return schema.DIRECTION_LONG
    return None


def _find_symbol(text: str, known: list[str]) -> tuple[Optional[str], Optional[tuple[int, int]]]:
    known_norm = {normalize_symbol(s): s for s in known}
    for m in re.finditer(r"\S+", text):
        canon = known_norm.get(normalize_symbol(m.group()))
        if canon is not None:
            return canon, m.span()
    return None, None


def _blank_span(text: str, span: Optional[tuple[int, int]]) -> str:
    if not span:
        return text
    return text[: span[0]] + " " + text[span[1] :]


def _setup_residual(text: str, symbol_span: Optional[tuple[int, int]]) -> str:
    """Оставить от строки только «слова сетапа», убрав всё распознанное:
    инструмент, направление, группы «ключ+число», маркер «сетап». Оставшееся
    (напр. «1Н поглощение», «qm+ob», «поглощение манипуляции») отдаём в
    find_matching_labels. Одиночные числа (бэар-вход без ключа) не мешают —
    матч по словарю Entry идёт подстрокой."""
    s = _blank_span(text, symbol_span)
    for key in (_ENTRY_KEY, _STOP_KEY, _TAKE_KEY):
        s = re.sub(rf"(?:{key}){_SEP}(?:{_NUM})", " ", s, flags=re.IGNORECASE)
    for w in _SHORT_WORDS + _LONG_WORDS:
        s = re.sub(rf"\b{re.escape(w)}\w*", " ", s, flags=re.IGNORECASE)
    s = re.sub(rf"(?:{_SETUP_MARKER})", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"[.,;]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class ParsedTrade:
    raw_text: str
    direction: Optional[str] = None          # "Long" | "Short"
    symbol: Optional[str] = None             # каноничное имя из DEFAULT_SYMBOLS
    entry: Optional[float] = None
    stop: Optional[float] = None
    take: Optional[float] = None
    entry_raw: Optional[str] = None          # цены как написаны (для показа, с хвостовыми нулями)
    stop_raw: Optional[str] = None
    take_raw: Optional[str] = None
    entry_label: Optional[str] = None        # значение select-поля Entry
    ambiguous_setups: tuple[str, ...] = ()   # >1 кандидата — просим уточнить
    missing: tuple[str, ...] = ()            # обязательные поля, что не распознались

    @property
    def planned_rr(self) -> Optional[float]:
        """План RR = |вход-тейк| / |вход-стоп|. Нужны вход, стоп и тейк."""
        if self.entry is None or self.stop is None or self.take is None:
            return None
        risk = abs(self.entry - self.stop)
        if risk == 0:
            return None
        return round(abs(self.entry - self.take) / risk, 2)

    @property
    def is_complete(self) -> bool:
        """Достаточно ли данных, чтобы создать строку (тейк опционален)."""
        return not self.missing and not self.ambiguous_setups


def parse_trade_text(
    text: str,
    known_symbols: Optional[list[str]] = None,
    entry_options: Optional[list[str]] = None,
) -> ParsedTrade:
    known_symbols = known_symbols or DEFAULT_SYMBOLS
    entry_options = entry_options or schema.ENTRY_OPTIONS

    direction = _find_direction(text)
    symbol, symbol_span = _find_symbol(text, known_symbols)

    stop_r = _num_after(text, _STOP_KEY)
    take_r = _num_after(text, _TAKE_KEY)
    entry_r = _num_after(text, _ENTRY_KEY)
    stop, stop_raw = stop_r if stop_r else (None, None)
    take, take_raw = take_r if take_r else (None, None)
    entry, entry_raw = entry_r if entry_r else (None, None)
    if entry is None:
        # вход без ключа (напр. «buy usdcad 1.3610 sl ... tp ...») — первое
        # число, не совпадающее со стопом/тейком; символ гасим, чтобы цифры
        # инструмента (GER40 -> 40) не приняло за число.
        scan = _blank_span(text, symbol_span)
        for m in re.finditer(_NUM, scan):
            val = _to_float(m.group())
            if (stop is not None and val == stop) or (take is not None and val == take):
                continue
            entry, entry_raw = val, _clean_raw(m.group())
            break

    entry_label: Optional[str] = None
    ambiguous: tuple[str, ...] = ()
    candidates = find_matching_labels(_setup_residual(text, symbol_span), entry_options)
    if len(candidates) == 1:
        entry_label = candidates[0]
    elif len(candidates) > 1:
        ambiguous = tuple(candidates)

    missing: list[str] = []
    if direction is None:
        missing.append("направление (лонг/шорт)")
    if symbol is None:
        missing.append("инструмент")
    if entry is None:
        missing.append("цена входа")
    if stop is None:
        missing.append("стоп")

    return ParsedTrade(
        raw_text=text,
        direction=direction,
        symbol=symbol,
        entry=entry,
        stop=stop,
        take=take,
        entry_raw=entry_raw,
        stop_raw=stop_raw,
        take_raw=take_raw,
        entry_label=entry_label,
        ambiguous_setups=ambiguous,
        missing=tuple(missing),
    )


def _fmt_num(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{x:g}"


def _disp(raw: Optional[str], val: Optional[float]) -> str:
    """Показываем как трейдер написал (с хвостовыми нулями), иначе из float."""
    return raw if raw is not None else _fmt_num(val)


def format_card(parsed: ParsedTrade) -> str:
    """Карточка разобранной сделки для подтверждения перед записью в Notion."""
    dir_ru = {schema.DIRECTION_SHORT: "Short (шорт)", schema.DIRECTION_LONG: "Long (лонг)"}
    lines = ["🧾 Разобрал сделку:"]
    lines.append(f"Направление: {dir_ru.get(parsed.direction, '— не распознал')}")
    lines.append(f"Инструмент: {parsed.symbol or '— не распознал'}")
    lines.append(f"Вход: {_disp(parsed.entry_raw, parsed.entry)}")
    lines.append(f"Стоп: {_disp(parsed.stop_raw, parsed.stop)}")
    lines.append(f"Тейк: {_disp(parsed.take_raw, parsed.take)}")

    if parsed.entry_label:
        lines.append(f"Сетап (Entry): {parsed.entry_label}")
    elif parsed.ambiguous_setups:
        lines.append("Сетап (Entry): — не уверен, уточни: " + ", ".join(parsed.ambiguous_setups))
    else:
        lines.append("Сетап (Entry): — не распознал (заполнишь в Notion вручную)")

    rr = parsed.planned_rr
    if rr is not None:
        lines.append(f"План RR: {rr:g}")

    if parsed.missing:
        lines.append("\n⚠️ Не хватает обязательных полей: " + ", ".join(parsed.missing))
    if parsed.ambiguous_setups:
        lines.append("Уточни сетап и пришли /trade заново — иначе один вариант потеряется.")

    return "\n".join(lines)


def body_note(parsed: ParsedTrade) -> str:
    """Короткая заметка в тело новой страницы: цены и план RR (в журнале нет
    отдельных полей под вход/стоп/тейк)."""
    parts = [f"Вход {_disp(parsed.entry_raw, parsed.entry)}", f"Стоп {_disp(parsed.stop_raw, parsed.stop)}"]
    if parsed.take is not None:
        parts.append(f"Тейк {_disp(parsed.take_raw, parsed.take)}")
    rr = parsed.planned_rr
    if rr is not None:
        parts.append(f"План RR {rr:g}")
    return " · ".join(parts)
