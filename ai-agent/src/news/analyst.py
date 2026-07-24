"""Вызовы Claude для новостного модуля: недельный дайджест и разбор вышедшей
новости. Оба используют server-side web search — в фиде ForexFactory нет ни
факта выхода (actual), ни макро-контекста.
"""
from __future__ import annotations

import os
from typing import Optional

import anthropic

from .models import NewsEvent
from .prompts import build_digest_prompt, build_interpretation_prompt

MODEL = "claude-sonnet-5"  # та же модель, что и в ai_review/analyzer.py

# web_search_20260209 поддерживается claude-sonnet-5; результаты приходят
# блоками web_search_tool_result в том же ответе, отдельного клиентского
# исполнения не требуется.
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}

# stop_reason=pause_turn: серверный тул-цикл упёрся в лимит итераций — ответ
# нужно продолжить повторным запросом с тем же контентом (см. доку Anthropic).
MAX_CONTINUATIONS = 5


class NewsAnalyst:
    def __init__(self, api_key: Optional[str] = None):
        self.client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])

    def _call(self, system: str, user: str, max_tokens: int = 8000) -> str:
        messages: list[dict] = [{"role": "user", "content": user}]
        response = None
        for _ in range(MAX_CONTINUATIONS):
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                tools=[WEB_SEARCH_TOOL],
                messages=messages,
            )
            if response.stop_reason != "pause_turn":
                break
            messages = [
                {"role": "user", "content": user},
                {"role": "assistant", "content": response.content},
            ]
        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            return "Claude не вернул текстовый ответ (stop_reason=%s) — попробуй ещё раз позже." % response.stop_reason
        return text.strip()

    def weekly_digest(
        self, events: list[NewsEvent], instruments: list[str], utc_offset_hours: int
    ) -> str:
        system, user = build_digest_prompt(events, instruments, utc_offset_hours)
        return self._call(system, user)

    def interpret_event(
        self, event: NewsEvent, instruments: list[str], utc_offset_hours: int
    ) -> str:
        system, user = build_interpretation_prompt(event, instruments, utc_offset_hours)
        return self._call(system, user, max_tokens=4000)
