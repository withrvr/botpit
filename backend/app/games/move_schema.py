"""JSON Schema of a game's move union, shaped for LLM structured output.

Self-contained (``$defs`` inlined, no ``$ref``), no ``title``s, every object
closed with ``additionalProperties: false``. One schema per game: seat and
legality are enforced by the referee, not the schema.
"""

from __future__ import annotations

import functools
from typing import Any

from app.games import get_game
from app.games.base import AnyGame

_DROP = frozenset({"title", "discriminator", "$defs", "description"})


def move_json_schema(game: AnyGame) -> dict[str, Any]:
    return _by_slug(game.slug)


@functools.cache
def _by_slug(slug: str) -> dict[str, Any]:
    raw = get_game(slug).move_adapter.json_schema()
    out = _inline(raw, raw.get("$defs", {}))
    assert isinstance(out, dict)
    return out


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_inline(x, defs) for x in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        target = defs[node["$ref"].rsplit("/", 1)[1]]
        # siblings of $ref (e.g. a field description) win over the target's
        return _inline(
            {**target, **{k: v for k, v in node.items() if k != "$ref"}}, defs
        )
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROP:
            continue
        if key == "properties":
            out[key] = {name: _inline(v, defs) for name, v in value.items()}
        else:
            out[key] = _inline(value, defs)
    if out.get("type") == "object":
        out["additionalProperties"] = False
        # A discriminator has a default in pydantic, so it is not "required" in
        # the emitted schema; constrained generation then omits it and the move
        # cannot be parsed. Every const/enum-of-one property must be present.
        consts = [k for k, v in out.get("properties", {}).items() if "const" in v]
        if consts:
            out["required"] = sorted(set(out.get("required", [])) | set(consts))
    return out


__all__ = ["move_json_schema"]
