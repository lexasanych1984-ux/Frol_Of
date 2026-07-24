"""Telegram-бот — интерфейс новостного модуля: /week и /news.

Хендлеры дёргают уже рабочий модуль src/news/ (календарь ForexFactory +
разборы через Claude с web search) — без собственной бизнес-логики поверх.

Команды /risk, /review, /setup удалены из бота по решению трейдера
2026-07-09 (резервная копия старого кода — bot.py.bak-2026-07-09 рядом);
сами модули risk/ai_review/setup_check остались в проекте и доступны из CLI.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from src.mt5_import import sync as mt5_sync
from src.mt5_import.live_export import MT5NotRunningError
from src.news import service as news_service
from src.news.analyst import NewsAnalyst
from src.notion_sync.client import NotionClient
from src.notion_sync.journal_writer import (
    JournalResolvers,
    build_trade_properties,
    create_journal_row,
)
from src.trade_log import parser as trade_parser

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4000  # запас от жёсткого лимита Telegram в 4096 символов


def _is_authorized(update: Update) -> bool:
    """Бот отвечает только владельцу — иначе финансовые данные и Claude/Notion
    вызовы доступны любому, кто найдёт бота по имени в Telegram."""
    allowed_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    return not allowed_chat_id or str(update.effective_chat.id) == str(allowed_chat_id)


def _chunk_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Недельный дайджест и разборы новостей легко превышают лимит Telegram
    на длину сообщения (4096 символов) — reply_text() падает с
    'Message is too long' и результат теряется молча (без хендлера ошибок)."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            if current:
                chunks.append(current)
            current = line
            while len(current) > limit:  # одна строка длиннее лимита — жёсткий разрез
                chunks.append(current[:limit])
                current = current[limit:]
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def _reply_long(update: Update, text: str) -> None:
    for chunk in _chunk_message(text):
        await update.message.reply_text(chunk)


# --- Новостной модуль (/week, /news + фоновый цикл) ---------------------------
# Календарь — ForexFactory weekly feed; дайджест недели и разборы вышедших
# новостей делает Claude с web search (src/news/). ПК не работает 24/7, поэтому
# фоновый цикл на каждом тике "догоняет" пропущенное: невысланный воскресный
# дайджест и вышедшие, но не разобранные новости (не старше catchup-окна).


def _run_week_digest(force: bool) -> str:
    cfg = news_service.load_news_config()
    conn = news_service.open_db()
    now = news_service.store.now_utc()
    news_service.refresh_calendar(conn, cfg)
    week = news_service.current_week_key(now)
    if not force and news_service.store.digest_sent_at(conn, week) is not None:
        return "Дайджест на эту неделю уже отправлял. Хочешь свежий — /week force"
    return news_service.build_digest(conn, cfg, NewsAnalyst(), week, now)


def _run_news_check() -> list[str]:
    cfg = news_service.load_news_config()
    conn = news_service.open_db()
    now = news_service.store.now_utc()
    news_service.refresh_calendar(conn, cfg)
    results = news_service.process_due_events(conn, cfg, NewsAnalyst(), now)
    if results:
        return [_format_news_push(e, text, cfg.utc_offset_hours) for e, text in results]

    upcoming = news_service.upcoming_today(conn, cfg, now)
    if not upcoming:
        return ["Свежих неразобранных новостей нет, и до конца дня красных новостей не ожидается."]
    lines = [
        f"{e.local_time(cfg.utc_offset_hours).strftime('%H:%M')} {e.country} — {e.title}"
        for e in upcoming
    ]
    return ["Свежих неразобранных новостей нет. Ещё сегодня (UTC+%d):\n%s" % (cfg.utc_offset_hours, "\n".join(lines))]


def _format_news_push(event, text: str, utc_offset_hours: int) -> str:
    header = (
        f"📰 {event.country} — {event.title} "
        f"({event.local_time(utc_offset_hours).strftime('%d.%m %H:%M')})"
    )
    return f"{header}\n\n{text}"


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    force = bool(context.args) and context.args[0].lower() == "force"
    await update.message.reply_text("Собираю календарь недели и прошу Claude сделать обзор — до пары минут…")
    digest = await asyncio.to_thread(_run_week_digest, force)
    await _reply_long(update, digest)


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text("Проверяю, что вышло, и разбираю через Claude…")
    messages = await asyncio.to_thread(_run_news_check)
    for msg in messages:
        await _reply_long(update, msg)


def _news_background_tick() -> list[str]:
    """Один проход фонового цикла. Возвращает сообщения для отправки владельцу."""
    cfg = news_service.load_news_config()
    conn = news_service.open_db()
    now = news_service.store.now_utc()
    news_service.refresh_calendar(conn, cfg)

    out: list[str] = []
    week = news_service.digest_due(conn, cfg, now)
    if week is not None:
        out.append("🗓 Обзор предстоящей недели\n\n" + news_service.build_digest(conn, cfg, NewsAnalyst(), week, now))
    for event, text in news_service.process_due_events(conn, cfg, NewsAnalyst(), now):
        out.append(_format_news_push(event, text, cfg.utc_offset_hours))
    return out


async def _news_loop(app: Application) -> None:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        logger.warning("TELEGRAM_CHAT_ID не задан — автопуш новостей выключен, доступны только /week и /news")
        return
    poll_minutes = news_service.load_news_config().poll_minutes
    while True:
        try:
            for msg in await asyncio.to_thread(_news_background_tick):
                for chunk in _chunk_message(msg):
                    await app.bot.send_message(chat_id=chat_id, text=chunk)
        except Exception:
            logger.exception("Новостной цикл: тик упал, продолжу на следующем")
        await asyncio.sleep(poll_minutes * 60)


# --- /sync и /trade (журнал сделок) ------------------------------------------
# Обе команды ПИШУТ в живой Notion-журнал, поэтому обе работают через
# предпросмотр -> inline-кнопка Подтвердить/Отмена -> запись (walkthrough-правило,
# память notion-changes-require-walkthrough). Ничего не пишется без подтверждения.

CONFIG_PATH = "config/config.yaml"

# MetaTrader5 держит одно подключение на процесс — сериализуем доступ к MT5,
# чтобы два одновременных /sync не подрались за единственное подключение.
_MT5_LOCK = asyncio.Lock()

# Отложенные подтверждения: токен -> {"kind", "created_at", "payload"}. Payload
# не влезает в callback_data (лимит 64 байта), поэтому храним здесь, а в кнопке —
# только короткий токен.
_PENDING: dict[str, dict] = {}
_PENDING_TTL_SECONDS = 3600

_notion_client_singleton: NotionClient | None = None


def _notion_client() -> NotionClient:
    global _notion_client_singleton
    if _notion_client_singleton is None:
        _notion_client_singleton = NotionClient()
    return _notion_client_singleton


def _load_mt5_sync_config(path: str = CONFIG_PATH) -> dict:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return raw.get("mt5_sync") or {}


def _stash_pending(kind: str, payload: dict) -> str:
    now = time.time()
    for tok in [t for t, v in _PENDING.items() if now - v["created_at"] > _PENDING_TTL_SECONDS]:
        _PENDING.pop(tok, None)
    token = uuid.uuid4().hex[:8]
    _PENDING[token] = {"kind": kind, "created_at": now, "payload": payload}
    return token


def _confirm_keyboard(kind: str, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data=f"{kind}:ok:{token}"),
                InlineKeyboardButton("✖️ Отмена", callback_data=f"{kind}:cancel:{token}"),
            ]
        ]
    )


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    cfg = _load_mt5_sync_config()
    terminal_path = cfg.get("terminal_path")
    if not terminal_path:
        await update.message.reply_text("В config.yaml не задан mt5_sync.terminal_path.")
        return

    lookback = cfg.get("default_lookback_days", 30)
    if context.args:
        try:
            lookback = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Аргумент /sync — число дней назад (например, /sync 60).")
            return

    date_to = datetime.now()
    date_from = date_to - timedelta(days=lookback)
    await update.message.reply_text(
        f"Подключаюсь к ручному терминалу MT5 и сверяю сделки за {lookback} дн. с журналом…"
    )

    try:
        async with _MT5_LOCK:
            preview = await asyncio.to_thread(
                mt5_sync.build_sync_preview, terminal_path, _notion_client(), date_from, date_to
            )
    except MT5NotRunningError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return
    except Exception:
        logger.exception("/sync: предпросмотр упал")
        await update.message.reply_text("Не смог собрать предпросмотр /sync — см. логи бота.")
        return

    text = mt5_sync.format_preview(preview)
    if not preview.candidates:
        await _reply_long(update, text)
        return

    token = _stash_pending("sync", {"candidates": preview.candidates, "account_login": preview.account_login})
    kb = _confirm_keyboard("sync", token)
    if len(text) <= TELEGRAM_MESSAGE_LIMIT:
        await update.message.reply_text(text, reply_markup=kb)
    else:
        # длинный список — кнопку нельзя прицепить к разрезанному сообщению
        await _reply_long(update, text)
        await update.message.reply_text(
            f"👆 Создать {len(preview.candidates)} строк в журнале?", reply_markup=kb
        )


async def cmd_trade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    # берём весь текст после "/trade", а не context.args — чтобы сохранить запятые
    text = (update.message.text or "").partition(" ")[2].strip()
    if not text:
        await update.message.reply_text(
            "Опиши сделку одной строкой, например:\n"
            "/trade шорт GER40, вход 24100 стоп 24150 тейк 24000, сетап 1Н поглощение"
        )
        return

    parsed = trade_parser.parse_trade_text(text)
    card = trade_parser.format_card(parsed)

    if not parsed.is_complete:
        # не хватает полей или сетап неоднозначен — просим поправить и прислать заново
        await update.message.reply_text(card)
        return

    token = _stash_pending("trade", {"parsed": parsed, "date": datetime.now()})
    await update.message.reply_text(
        card + "\n\nСоздать строку в журнале с этим Entry?",
        reply_markup=_confirm_keyboard("trade", token),
    )


def _apply_sync_blocking(payload: dict) -> tuple[int, list[str]]:
    return mt5_sync.apply_sync(
        _notion_client(), payload["candidates"], payload["account_login"]
    )


def _apply_trade_blocking(payload: dict) -> list[str]:
    parsed = payload["parsed"]
    resolvers = JournalResolvers(_notion_client())
    props, warnings = build_trade_properties(
        resolvers,
        symbol=parsed.symbol,
        direction=parsed.direction,
        date=payload["date"],
        entry_label=parsed.entry_label,
    )
    create_journal_row(_notion_client(), props, body_note=trade_parser.body_note(parsed))
    return warnings


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_authorized(update):
        await query.answer()
        return
    await query.answer()

    kind, _, rest = query.data.partition(":")
    action, _, token = rest.partition(":")
    entry = _PENDING.pop(token, None)
    if entry is None:
        await query.edit_message_text("Это подтверждение уже неактуально (или устарело). Запусти команду заново.")
        return

    if action == "cancel":
        await query.edit_message_text("Отменено — в журнал ничего не записал.")
        return

    payload = entry["payload"]
    try:
        if kind == "sync":
            created, warnings = await asyncio.to_thread(_apply_sync_blocking, payload)
            msg = f"✅ Создал строк в журнале: {created}."
            if warnings:
                msg += "\n\nНо учти:\n— " + "\n— ".join(warnings)
        else:  # trade
            warnings = await asyncio.to_thread(_apply_trade_blocking, payload)
            label = payload["parsed"].entry_label
            msg = f"✅ Записал сделку в журнал. Entry: {label}."
            if warnings:
                msg += "\n\nНо учти:\n— " + "\n— ".join(warnings)
    except Exception:
        logger.exception("Подтверждение %s: запись в Notion упала", kind)
        await query.edit_message_text("Не смог записать в Notion — см. логи бота, в журнале ничего не изменилось.")
        return

    await query.edit_message_text(msg)


async def _post_init(app: Application) -> None:
    """Регистрирует список команд в Telegram — то, что показывается
    всплывающей подсказкой при вводе "/" в чате с ботом."""
    await app.bot.set_my_commands(
        [
            BotCommand("week", "Обзор красных новостей на неделю + макро-сценарии"),
            BotCommand("news", "Разобрать вышедшие новости / показать что впереди"),
            BotCommand("sync", "Импорт закрытых сделок из MT5 в журнал Notion"),
            BotCommand("trade", "Быстрый лог сделки текстом (заполняет Entry)"),
        ]
    )
    # фоновый цикл: автопуш дайджеста и разборов, пока бот запущен
    app.create_task(_news_loop(app))


def build_app() -> Application:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).post_init(_post_init).build()
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CommandHandler("trade", cmd_trade))
    app.add_handler(CallbackQueryHandler(on_confirm, pattern=r"^(sync|trade):(ok|cancel):"))
    return app


if __name__ == "__main__":
    build_app().run_polling()
