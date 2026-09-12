"""Agent-side consumers of the privacy gate: prompts and personas.

Everything here reads ``PlayerView`` only (DECISIONS.md §9). Phase 5 adds the
client adapters and the turn loop.
"""

from app.agents.persona import PERSONAS, Persona, get_persona
from app.agents.prompt import (
    PROMPT_TOKEN_BUDGET,
    PromptBundle,
    build_chat_prompt,
    build_move_prompt,
    estimate_tokens,
)

__all__ = [
    "PERSONAS",
    "PROMPT_TOKEN_BUDGET",
    "Persona",
    "PromptBundle",
    "build_chat_prompt",
    "build_move_prompt",
    "estimate_tokens",
    "get_persona",
]
