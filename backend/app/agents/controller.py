"""The LLM-backed seat controller: two model calls per turn (DECISIONS §5)."""

from __future__ import annotations

import functools
import json
import time
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
from app.metrics import (
    AGENT_TURN_ERRORS_TOTAL,
    AGENT_TURN_LATENCY_SECONDS,
    note_llm_call,
)
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
        # Set by `Referee.__init__` (duck-typed, same pattern as `say`/`hear`
        # elsewhere in the referee) once the referee's own `game_id` exists --
        # this controller is constructed before that, so it starts as None.
        # Without it, every event this controller publishes carries no
        # `correlation.game_id` and `RoomService._archive_game` (Phase 9,
        # docs/games.md) silently never archives it.
        self.game_id: str | None = None

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
            call_start = time.perf_counter()
            try:
                note_llm_call(self.client.base_url, self.model)
                result = await with_retries(
                    functools.partial(self.client.complete, req)
                )
            except LLMError as exc:
                # Real elapsed wall-clock time, not 0 -- `with_retries` may
                # have already spent real time retrying before giving up,
                # and this is exactly the call this histogram most wants to
                # characterize accurately (a persistent failure, possibly
                # after a VRAM-swap-induced slow start).
                elapsed_ms = (time.perf_counter() - call_start) * 1000
                self._metrics(latency_ms=elapsed_ms, ok=False, error=type(exc).__name__)
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
            if "seat" not in move:
                move = move | {
                    "seat": self.seat
                }  # the prompt says every move carries it
            try:
                parsed = self.game.parse_move(move)
            except ValidationError as exc:
                first = exc.errors()[0]
                last_error = f"invalid move: {first.get('msg')}"
                continue
            if parsed.seat != self.seat:
                last_error = f"move.seat must be {self.seat}"
                continue
            normalised: dict[str, Any] = parsed.model_dump(mode="json")
            if view.legal_moves and not any(
                _compatible(normalised, legal) for legal in view.legal_moves
            ):
                # Schema-valid but not playable now: the view already lists what is.
                # The referee stays the authority; this saves a round trip there.
                last_error = (
                    f"{self.game.describe_move(parsed)} is not one of legal_moves; "
                    "copy one of them exactly"
                )
                continue
            return normalised
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
            data = json.loads(result.text)
        except (LLMError, ValueError) as exc:
            log.info("chat call failed for seat %s: %s", self.seat, exc)
            return None
        text = data.get("say") if isinstance(data, dict) else None
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
        AGENT_TURN_LATENCY_SECONDS.labels(model=self.model).observe(
            fields.get("latency_ms", 0) / 1000
        )
        error = fields.get("error")
        if error is not None:
            AGENT_TURN_ERRORS_TOTAL.labels(model=self.model, error=error).inc()

    async def _publish(self, payload: Any, visibility: Any) -> None:
        correlation = {"game_id": self.game_id} if self.game_id else {}
        await self.bus.publish(
            make_event(self.room_id, payload, visibility, correlation=correlation)
        )


def _compatible(candidate: dict[str, Any], legal: dict[str, Any]) -> bool:
    """Equal on every field both sides set; ``None`` (omitted echo fields) matches."""
    return all(
        candidate.get(k) is None
        or legal.get(k) is None
        or candidate.get(k) == legal.get(k)
        for k in candidate.keys() | legal.keys()
    )


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
