"""Имена свойств базы Notion "Trading Journal" и "Account".

Сняты напрямую со схемы через Notion MCP 2026-07-01 — см.
notion_schema.md в корне проекта (или в первой переписке с Claude) для
полного разбора. Заводить константы здесь, а не хардкодить строки по
всему коду, чтобы при переименовании поля в Notion правки были в одном
месте.

ВАЖНО (проверено на практике): запрос данных через SQL/view режимы
Notion MCP (`query_data_sources`) на текущем плане воркспейса упирается
в "requires a Business plan or higher with Notion AI". Обычный REST API
(`notion-client`, метод `databases.query` / `data_sources.query` в
API-версии 2025-09+) должен работать без этого ограничения — но это
нужно проверить отдельно при первом реальном запуске client.py, так как
это ограничение снималось только со стороны конкретного MCP-инструмента,
не факт что относится и к прямым вызовам REST API.
"""

# --- data source ids (см. .env.example) ---
# 2026-07-08: журнал восстановлен трейдером из копии, id сменился
# (старый e0613367-... указывает на удалённую базу)
TRADING_JOURNAL_DATA_SOURCE_ID = "1a913367-6109-8288-a469-87010fe0d64b"
ACCOUNT_DATA_SOURCE_ID = "26113367-6109-8341-a274-87ea0e356178"

# Контейнер-БД журнала (parent для создания страниц, если API откажет в
# parent по data_source_id). Снят из URL страницы data source 2026-07-22.
TRADING_JOURNAL_DATABASE_ID = "ede13367-6109-83f2-ab56-8171a3e143be"

# Справочники relation-полей журнала (title-свойство у каждого своё) — нужны,
# чтобы при СОЗДАНИИ строки резолвить название в id связанной страницы.
# Сняты read-only со схемы 2026-07-22.
PAIRS_DATA_SOURCE_ID = "98713367-6109-824f-a0a1-0777dc5fb388"       # title: "Name"
DIRECTION_DATA_SOURCE_ID = "f7c13367-6109-8319-b8e0-8732954a012b"  # title: "Direction"
SETUP_DATA_SOURCE_ID = "3e513367-6109-8247-a48e-87e32b517774"      # title: "Name"

# Значения select-поля Entry (закрытый словарь, снят со схемы 2026-07-22).
# /trade фаззи-матчит слово-сетап трейдера на эти значения (см.
# setup_check.finder.find_matching_labels). При изменении списка в Notion —
# обновить здесь.
ENTRY_OPTIONS = [
    "QM+OB",
    "QM+FVG",
    "FVG 1H",
    "QM+market",
    "1Н поглощение манипуляции",
    "5m поглощение манипуляции",
    "limit-int liq",
]

# MT5 buy/sell -> название страницы в справочнике Direction.
DIRECTION_LONG = "Long"
DIRECTION_SHORT = "Short"

# --- свойства "Trading Journal" (сделка = одна запись) ---
class TradingJournal:
    TITLE = "№"                       # title
    DATE = "Date"                     # date, время входа — точка сведения с MT5
    DAY = "Day"                       # select, авто
    ACCOUNT = "Account"               # relation -> Account
    PAIRS = "Pairs"                   # relation -> Pairs (инструмент)
    DIRECTION = "Direction"           # relation -> Direction (buy/sell)
    SESSIONS = "Sessions"             # relation -> Sessions
    SETUP = "Setup"                   # relation -> Setup
    ENTRY = "Entry"                   # select: QM+OB, QM+FVG, FVG 1H, QM+market,
                                       #         "1Н поглощение манипуляции",
                                       #         "5m поглощение манипуляции", limit-int liq
    STRATEGY = "Strategy"             # relation -> Strategy
    RESULT = "Result"                 # select: TP | BE | SL | Missed
    RR_PNL = "RR/PnL"                 # number, ручной ввод (RR без знака или PnL со знаком $)
    RISK_PCT = "Risk %"               # number (percent)
    MADE_A_MISTAKE = "Made a mistake?"  # checkbox
    MISTAKE = "Mistake"               # multi_select: Эмоции, Статистический, FTA,
                                       #   "я дебил", "не было шифта", "не моя ТС"
    THE_BEST_TRADE = "The Best Trade?"  # checkbox
    NOTES = "Notes"                   # relation -> Notes
    SYSTEMATIC_ERRORS = "Systematic Errors"  # relation -> Systematic Errors
    # Дедупликация: одна реальная сделка, открытая на нескольких проп-счетах,
    # = несколько записей журнала. Дубль ссылается на "главную" запись через
    # Main Trade (limit 1); обратная сторона той же relation — Sub Trades.
    # Запись с пустым Main Trade = уникальная сделка (для честного win rate).
    MAIN_TRADE = "Main Trade"         # relation -> Trading Journal (self), limit 1
    SUB_TRADES = "Sub Trades"         # relation -> Trading Journal (self), обратная сторона


# --- свойства "Account" (один проп-счёт = одна запись) ---
class Account:
    NAME = "Name"                     # title
    STATUS = "Status"                 # select: Live, Demo, Step 1/2/3, Challenge, Funded
    DEPOSIT = "Deposit"                # number ($)
    TARGET_PCT = "Target (%)"         # number
    DATE_OPEN = "Data open"           # date
    DATE_CLOSE = "Date close"         # date
    TRADING_JOURNAL = "Trading Journal"  # relation -> Trading Journal

    # ПРОБЕЛ (на момент разбора базы): нет полей max daily loss % /
    # max overall drawdown % / challenge_type — эти цифры для риск-модуля
    # берутся из config/config.yaml, а не из Notion. Если захочется вести
    # их в Notion тоже — добавить эти поля в базу и сюда.
