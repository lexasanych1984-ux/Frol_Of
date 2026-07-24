"""Периодический AI-разбор сделок через Claude API."""
from __future__ import annotations

import json
import os
from typing import Optional

import anthropic

from src.matching.matcher import MatchResult
from src.notion_sync import schema
from .prompts import build_review_prompt

MODEL = "claude-sonnet-5"  # см. актуальный список моделей в доке Anthropic перед запуском


def _match_to_dict(m: MatchResult) -> dict:
    d: dict = {}
    if m.trade:
        d.update(
            symbol=m.trade.symbol,
            direction=m.trade.direction,
            open_time=m.trade.open_time.isoformat() if m.trade.open_time else None,
            close_time=m.trade.close_time.isoformat() if m.trade.close_time else None,
            volume=m.trade.volume,
            net_pnl=round(m.trade.net_pnl, 2),
        )
    if m.notion_page:
        props = m.notion_page.get("properties", {})
        d.update(
            result=(props.get(schema.TradingJournal.RESULT, {}).get("select") or {}).get("name"),
            risk_pct=(props.get(schema.TradingJournal.RISK_PCT, {}) or {}).get("number"),
            rr_pnl=(props.get(schema.TradingJournal.RR_PNL, {}) or {}).get("number"),
            made_a_mistake=(props.get(schema.TradingJournal.MADE_A_MISTAKE, {}) or {}).get("checkbox"),
            mistake_tags=[t["name"] for t in (props.get(schema.TradingJournal.MISTAKE, {}) or {}).get("multi_select", [])],
        )
    d["matched"] = m.matched
    return d


class AIReviewer:
    def __init__(self, api_key: Optional[str] = None):
        self.client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])

    def review(self, matches: list[MatchResult], period: str, min_sample_size: int = 30) -> str:
        records = [_match_to_dict(m) for m in matches]
        system, user = build_review_prompt(
            period=period,
            n_trades=len(records),
            trades_json=json.dumps(records, ensure_ascii=False, indent=2),
            min_sample_size=min_sample_size,
        )
        response = self.client.messages.create(
            model=MODEL,
            # 2000 был недостаточен: модель по умолчанию использует extended
            # thinking, и весь бюджет уходил на размышления, оставляя пустой
            # текст ответа (stop_reason=max_tokens, 0 текстовых токенов).
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in response.content if block.type == "text")
