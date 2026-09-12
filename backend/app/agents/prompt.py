"""Prompt builders for the two agent calls (DECISIONS.md §5).

Both builders take a ``PlayerView`` and nothing else from the game: this
module is the agent-side consumer of the privacy gate (DECISIONS.md §9) and
a test asserts it never touches the full state.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from app.agents.persona import Persona
from app.events.schema import ChatMessage
from app.games.base import AnyGame, Frozen, PlayerView
from app.games.move_schema import move_json_schema

PROMPT_TOKEN_BUDGET = 3500
MAX_CHAT_LINES = 10
_HOW_TO_THINK = re.compile(r"\n## How to think\n.*?(?=\n## |\Z)", re.DOTALL)


class PromptBundle(Frozen):
    system: str
    user: str
    json_schema: dict[str, Any]


def estimate_tokens(text: str) -> int:
    return len(text) // 4  # ponytail: chars/4, swap for a tokenizer if budgets bite


def build_move_prompt(
    game: AnyGame,
    view: PlayerView,
    persona: Persona,
    recent_chat: Sequence[ChatMessage],
    last_error: str | None,
) -> PromptBundle:
    schema = {
        "type": "object",
        "properties": {
            "reasoning": {"type": "string"},
            "move": move_json_schema(game),
        },
        "required": ["reasoning", "move"],
        "additionalProperties": False,
    }
    rules = game.rules_markdown()
    chat = list(recent_chat[-MAX_CHAT_LINES:])
    while True:
        system = _move_system(rules, persona)
        user = _move_user(view, chat, last_error)
        if estimate_tokens(system + user) <= PROMPT_TOKEN_BUDGET:
            break
        if chat:
            chat.pop(0)
        elif _HOW_TO_THINK.search(rules):
            rules = _HOW_TO_THINK.sub("", rules)
        else:
            break  # nothing left to trim; the view itself is the floor
    return PromptBundle(system=system, user=user, json_schema=schema)


def build_chat_prompt(
    game: AnyGame,
    view: PlayerView,
    persona: Persona,
    move_description: str,
    recent_chat: Sequence[ChatMessage],
) -> PromptBundle:
    system = (
        f"You are seat {view.seat} at a game of {game.title}, playing as "
        f'"{persona.name}". Voice: {persona.voice}\n'
        "You just made your move. You may add one short line of table talk "
        "(under 200 characters) in that voice, or stay silent.\n"
        "Never reveal or hint at the cards in your own hand. "
        f"Speak roughly {int(persona.chattiness * 100)}% of the time; "
        'otherwise answer {"say": null}.\n'
        'Respond with ONE JSON object: {"say": "<text>"} or {"say": null}.'
    )
    public = {
        "phase": view.phase,
        "hand_counts": view.hand_counts,
        "scores": view.scores,
        "table": view.table,
    }
    user = (
        f"Your move: {move_description}\n"
        f"Table: {_compact(public)}\n"
        f"{_chat_block(list(recent_chat[-MAX_CHAT_LINES:]))}"
    )
    schema = {
        "type": "object",
        "properties": {"say": {"type": ["string", "null"], "maxLength": 200}},
        "required": ["say"],
        "additionalProperties": False,
    }
    return PromptBundle(system=system, user=user, json_schema=schema)


def _move_system(rules: str, persona: Persona) -> str:
    counting = (
        "Track which cards have been played and what each opponent is likely to hold."
        if persona.counts_cards
        else "Do not bother counting cards; play the table in front of you."
    )
    return (
        f"{rules.rstrip()}\n\n"
        "## Persona\n"
        f'You play as "{persona.name}". Risk appetite: {persona.risk}. {counting}\n'
        f"Voice: {persona.voice}\n\n"
        "## Output\n"
        "Respond with ONE JSON object matching the schema, with exactly two "
        "top-level keys:\n"
        '- "reasoning": 1-3 sentences, private, never shown to other players.\n'
        '- "move": one of the entries in `legal_moves`, copied exactly.\n'
    )


def _move_user(
    view: PlayerView, chat: list[ChatMessage], last_error: str | None
) -> str:
    parts = [
        f"Game state (you are seat {view.seat}):\n{_compact(view.model_dump(mode='json'))}"
    ]
    if chat:
        parts.append(_chat_block(chat))
    if last_error:
        parts.append(
            f"Your previous move was rejected: {last_error}. "
            "Choose a different move from legal_moves."
        )
    return "\n\n".join(parts)


def _chat_block(chat: list[ChatMessage]) -> str:
    if not chat:
        return "Recent chat: (none)"
    return "Recent chat:\n" + "\n".join(f"seat {m.seat}: {m.text}" for m in chat)


def _compact(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"))


__all__ = [
    "MAX_CHAT_LINES",
    "PROMPT_TOKEN_BUDGET",
    "PromptBundle",
    "build_chat_prompt",
    "build_move_prompt",
    "estimate_tokens",
]
