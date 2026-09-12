"""Tail sampling: decide after the outcome is known whether a wide event is kept."""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


class TailSampler(logging.Filter):
    """Keeps errors, slow events and ``force_keep``; samples the rest at ``sample_rate``.

    Attached to the ``botpit.wide`` logger only, so ordinary log lines are untouched.
    """

    def __init__(
        self, sample_rate: float, slow_ms: float, rng: random.Random | None = None
    ) -> None:
        super().__init__()
        self.sample_rate = sample_rate
        self.slow_ms = slow_ms
        self.rng = rng or random.Random()

    def keep(self, fields: Mapping[str, Any]) -> bool:
        if fields.get("outcome") == "error" or fields.get("force_keep"):
            return True
        duration = fields.get("duration_ms")
        if isinstance(duration, int | float) and duration >= self.slow_ms:
            return True
        return self.rng.random() < self.sample_rate

    def filter(self, record: logging.LogRecord) -> bool:
        wide = getattr(record, "wide", None)
        return not isinstance(wide, dict) or self.keep(wide)
