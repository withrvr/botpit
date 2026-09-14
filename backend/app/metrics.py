"""Prometheus metrics (Phase 13, DECISIONS.md §14): active games, active
websocket connections, agent turn latency/errors per model, moves,
model swaps. Complements the wide-event log (``app/telemetry``) rather
than duplicating it -- wide events are for debugging one request/turn/
game after the fact; these are for graphing trends over time.

A dedicated ``CollectorRegistry`` rather than ``prometheus_client``'s
process-global default: keeps ``GET /metrics`` explicit about exactly
what it exposes, and avoids the classic "duplicated metric name" footgun
if this module is ever imported twice under different names (it happens
in test collection more often than you'd expect).
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

ACTIVE_GAMES = Gauge(
    "botpit_active_games", "Rooms currently in_progress", registry=REGISTRY
)
ACTIVE_WS_CONNECTIONS = Gauge(
    "botpit_active_ws_connections",
    "Open room websocket connections",
    registry=REGISTRY,
)
AGENT_TURN_LATENCY_SECONDS = Histogram(
    "botpit_agent_turn_latency_seconds",
    "Latency of one LLM completion call within an agent's turn",
    ["model"],
    # `prometheus_client`'s default buckets top out at 10s -- useless here:
    # `LLM_TIMEOUT_SECONDS` defaults to 120s, and a slow/VRAM-swapped local
    # model call landing anywhere past 10s (exactly what this metric exists
    # to characterize) would otherwise collapse into one +Inf bucket.
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 90, 120, float("inf")),
    registry=REGISTRY,
)
AGENT_TURN_ERRORS_TOTAL = Counter(
    "botpit_agent_turn_errors_total",
    "LLM completion calls that failed or produced an unusable reply",
    ["model", "error"],
    registry=REGISTRY,
)
AGENT_TURN_TIMEOUTS_TOTAL = Counter(
    "botpit_agent_turn_timeouts_total",
    "Turns that hit their deadline and fell back to the referee's own move",
    ["model"],
    registry=REGISTRY,
)
MOVES_TOTAL = Counter(
    "botpit_moves_total", "Moves applied across every game", registry=REGISTRY
)
MODEL_SWAPS_TOTAL = Counter(
    "botpit_model_swaps_total",
    "Consecutive LLM calls to the same backend that changed model -- a "
    "likely VRAM swap (DECISIONS.md §13); the turn scheduler that would "
    "actively minimise these remains a separate, not-yet-built feature, "
    "this just counts them",
    ["base_url"],
    registry=REGISTRY,
)

_last_model_by_base_url: dict[str, str] = {}


def note_llm_call(base_url: str, model: str) -> None:
    """Call once right before every real outbound LLM completion request.
    Increments ``MODEL_SWAPS_TOTAL`` when the immediately preceding call to
    this same ``base_url`` used a different model. Synchronous with no
    ``await`` between the read and the write, so concurrent asyncio tasks
    cannot race this -- the same reasoning ``ModelRegistry.client_for``
    already relies on for its own check-then-insert."""
    last = _last_model_by_base_url.get(base_url)
    if last is not None and last != model:
        MODEL_SWAPS_TOTAL.labels(base_url=base_url).inc()
    _last_model_by_base_url[base_url] = model
