"""The LLM-backed seat controller: two model calls per turn (DECISIONS §5)."""

from __future__ import annotations

import functools
import json
from collections.abc import Sequence
from typing import Any, ClassVar

from pydantic import ValidationError

from app.agents.persona import Persona
from app.agents.prompt import build_chat_prompt, build_move_prompt
from app.events import (
    PRIVILEGED,
    PUBLIC,
    AgentReasoning,
    ChatMessage,
    EventBus,
    make_event,
)
from app.games.base import AnyGame, PlayerView, Seat
from app.llm.client import CompletionRequest, LLMClient, LLMError, Message, with_retries
from app.referee.seats import ControllerError, SeatKind, TurnContext
from app.telemetry import current_event, get_logger

log = get_logger("agents")


class AgentController:
    kind: ClassVar[SeatKind] = "agent"

    def __init__(
        self,
        game: AnyGame,
        client: LLMClient,
        model: str,
        persona: Persona,
        bus: EventBus,
        room_id: str,
        seat: Seat,
        *,
        max_repairs: int = 2,
        temperature: float = 0.7,
        num_ctx: int | None = None,
        keep_alive: str | None = None,
        think: bool | None = False,
    ) -> None:
        self.game = game
        self.client = client
        self.model = model
        self.persona = persona
        self.bus = bus
        self.room_id = room_id
        self.seat = seat
        self.max_repairs = max_repairs
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        # Off by default: the prompt already asks for a written ``reasoning``
        # field; qwen3's separate thinking pass costs ~60 s per turn on a 6 GB card.
        self.think = think
        self.recent_chat: list[ChatMessage] = []
        self.last_move_description: str | None = None

    # -- call 1: reasoning + move -------------------------------------------

    async def decide(self, view: PlayerView, ctx: TurnContext) -> dict[str, Any]:
        last_error = ctx.last_error
        for repair in range(self.max_repairs + 1):
            bundle = build_move_prompt(
                self.game, view, self.persona, self.recent_chat, last_error
            )
            req = CompletionRequest(
                model=self.model,
                messages=(
                    Message(role="system", content=bundle.system),
                    Message(role="user", content=bundle.user),
                ),
                json_schema=bundle.json_schema,
                temperature=self.temperature,
                seed=None,
                num_ctx=self.num_ctx,
                keep_alive=self.keep_alive,
                think=self.think,
            )
            try:
                result = await with_retries(
                    functools.partial(self.client.complete, req)
                )
            except LLMError as exc:
                self._metrics(latency_ms=0, ok=False, error=type(exc).__name__)
                raise ControllerError(f"{self.model}: {exc}") from exc
            reasoning, move = _parse_answer(result.text)
            if result.reasoning:
                reasoning = (
                    f"{result.reasoning}\n{reasoning}".strip()
                    if reasoning
                    else result.reasoning
                )
            if reasoning:
                await self._publish(
                    AgentReasoning(seat=self.seat, model=self.model, text=reasoning),
                    PRIVILEGED,
                )
            self._metrics(
                latency_ms=result.latency_ms,
                ok=move is not None,
                error=None if move is not None else "bad_json",
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                repairs=repair,
            )
            if move is None:
                last_error = (
                    "your reply was not a JSON object with 'reasoning' and 'move'"
                )
                continue
            try:
                parsed = self.game.parse_move(move)
            except ValidationError as exc:
                first = exc.errors()[0]
                last_error = f"invalid move: {first.get('msg')}"
                continue
            if parsed.seat != self.seat:
                move = move | {"seat": self.seat}
            return move
        raise ControllerError(
            f"{self.model}: no parseable move after {self.max_repairs + 1} tries"
        )

    # -- call 2: chat ------------------------------------------------------------

    async def say(self, view: PlayerView, move_description: str) -> str | None:
        bundle = build_chat_prompt(
            self.game, view, self.persona, move_description, self.recent_chat
        )
        req = CompletionRequest(
            model=self.model,
            messages=(
                Message(role="system", content=bundle.system),
                Message(role="user", content=bundle.user),
            ),
            json_schema=bundle.json_schema,
            temperature=min(1.0, self.temperature + 0.2),
            num_ctx=self.num_ctx,
            keep_alive=self.keep_alive,
            think=False,
        )
        try:
            result = await self.client.complete(req)
            text = json.loads(result.text).get("say")
        except (LLMError, ValueError, AttributeError) as exc:
            log.info("chat call failed for seat %s: %s", self.seat, exc)
            return None
        if not isinstance(text, str) or not text.strip():
            return None
        text = text.strip()[:200]
        msg = ChatMessage(seat=self.seat, text=text)
        self.recent_chat.append(msg)
        await self._publish(msg, PUBLIC)
        return text

    def hear(self, messages: Sequence[ChatMessage]) -> None:
        """Feed table chat from other seats (the referee/room layer calls this)."""
        self.recent_chat.extend(messages)
        self.recent_chat = self.recent_chat[-20:]

    # -- helpers ------------------------------------------------------------------

    def _metrics(self, **fields: Any) -> None:
        ev = current_event()
        if ev is not None:
            ev.add("llm.calls", {"model": self.model, **fields})

    async def _publish(self, payload: Any, visibility: Any) -> None:
        await self.bus.publish(make_event(self.room_id, payload, visibility))


def _parse_answer(text: str) -> tuple[str, dict[str, Any] | None]:
    """Return (reasoning, move) from the model's JSON; tolerate junk around it."""
    candidate = text.strip()
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end == -1:
        return "", None
    try:
        data = json.loads(candidate[start : end + 1])
    except ValueError:
        return "", None
    if not isinstance(data, dict):
        return "", None
    reasoning = data.get("reasoning")
    move = data.get("move")
    if not isinstance(move, dict):
        return str(reasoning or ""), None
    return str(reasoning or ""), move
