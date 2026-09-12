from __future__ import annotations

import json
import logging
import random
import sys
from typing import TYPE_CHECKING

import pytest

from botpit.config import Settings
from botpit.logging import configure_logging, current_event, get_logger, wide_event
from botpit.logging.json_formatter import ConsoleFormatter, JsonFormatter
from botpit.logging.sampling import TailSampler

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

WIDE = logging.getLogger("botpit.wide")
BASE = {"event_kind", "event_id", "ts", "service", "version", "env", "outcome", "duration_ms"}


@pytest.fixture(autouse=True)
def _clean_wide_logger() -> Iterator[None]:
    yield
    for flt in list(WIDE.filters):
        WIDE.removeFilter(flt)


def wide_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "botpit.wide"]


def test_emits_once_with_base_fields(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    with wide_event("unit.work", request_id="r1") as ev:
        ev.set(user_id="u1").add("steps", "a")
        ev.add("steps", "b")
        ev.incr("retries")
        assert current_event() is ev
    assert current_event() is None
    records = wide_records(caplog)
    assert len(records) == 1
    fields = records[0].wide  # type: ignore[attr-defined]
    assert fields.keys() >= BASE
    assert fields["event_kind"] == "unit.work"
    assert fields["service"] == "botpit"
    assert fields["outcome"] == "ok"
    assert fields["steps"] == ["a", "b"]
    assert fields["retries"] == 1
    assert fields["request_id"] == "r1"
    assert records[0].levelno == logging.INFO


async def test_async_context_manager(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    async with wide_event("async.work") as ev:
        assert current_event() is ev
    assert wide_records(caplog)[0].wide["outcome"] == "ok"  # type: ignore[attr-defined]


def test_nested_inherits_correlation_and_parent(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    with wide_event("outer", request_id="r1", game_id="g1", seat=2) as outer:
        with wide_event("inner", seat=3) as inner:
            assert current_event() is inner
        assert current_event() is outer
    inner_f, outer_f = (r.wide for r in wide_records(caplog))  # type: ignore[attr-defined]
    assert inner_f["parent_id"] == outer_f["event_id"]
    assert inner_f["request_id"] == "r1"
    assert inner_f["game_id"] == "g1"
    assert inner_f["seat"] == 3
    assert "parent_id" not in outer_f


def test_error_path_emits_error_and_propagates(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    with pytest.raises(ValueError, match="boom"), wide_event("failing"):
        raise ValueError("boom")
    (record,) = wide_records(caplog)
    assert record.levelno == logging.ERROR
    fields = record.wide  # type: ignore[attr-defined]
    assert fields["outcome"] == "error"
    assert fields["error.type"] == "ValueError"
    assert fields["error.message"] == "boom"
    assert fields["error.retriable"] is False
    assert "error.traceback" not in fields
    assert current_event() is None


def test_error_traceback_only_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RuntimeError), wide_event("failing"):
        raise RuntimeError("x")
    assert "RuntimeError: x" in wide_records(caplog)[0].wide["error.traceback"]  # type: ignore[attr-defined]


def test_sampler_keep_rules() -> None:
    zero = TailSampler(0.0, 100, rng=random.Random(0))
    assert zero.keep({"outcome": "error", "duration_ms": 1})
    assert zero.keep({"outcome": "ok", "duration_ms": 100})
    assert zero.keep({"outcome": "ok", "duration_ms": 1, "force_keep": True})
    assert not zero.keep({"outcome": "ok", "duration_ms": 1})
    one = TailSampler(1.0, 100, rng=random.Random(0))
    assert one.keep({"outcome": "ok", "duration_ms": 1})


def test_sampler_filters_only_wide_records(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    WIDE.addFilter(TailSampler(0.0, 10_000))
    with wide_event("dropped"):
        pass
    get_logger("plain").info("ordinary line")
    assert wide_records(caplog) == []
    assert [r.getMessage() for r in caplog.records] == ["ordinary line"]


def test_json_formatter_flattens_wide() -> None:
    record = logging.LogRecord("botpit.wide", logging.INFO, __file__, 1, "%s ok", ("k",), None)
    record.wide = {"event_kind": "k", "outcome": "ok", "weird": {1, 2}}
    line = JsonFormatter().format(record)
    data = json.loads(line)
    assert "\n" not in line
    assert data["event_kind"] == "k"
    assert data["outcome"] == "ok"
    assert data["msg"] == "k ok"
    assert data["logger"] == "botpit.wide"
    assert data.keys() >= {"ts", "level"}
    assert "wide" not in data
    assert "extra" not in data


def test_json_formatter_includes_exc_info() -> None:
    try:
        raise KeyError("nope")
    except KeyError:
        record = logging.LogRecord("botpit.x", logging.ERROR, __file__, 1, "bad", None, None)
        record.exc_info = sys.exc_info()
    assert "KeyError" in json.loads(JsonFormatter().format(record))["exc_info"]


def test_console_formatter_shows_wide_keys() -> None:
    record = logging.LogRecord("botpit.wide", logging.INFO, __file__, 1, "k ok", None, None)
    record.wide = {"event_kind": "k", "outcome": "ok", "duration_ms": 1.5, "error.type": "E"}
    line = ConsoleFormatter().format(record)
    assert "INFO botpit.wide k ok" in line
    assert "event_kind=k" in line
    assert "error.type=E" in line


def test_configure_logging_idempotent(tmp_path: Path) -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    settings = Settings(log_format="json", log_file=str(tmp_path / "botpit.log"))  # type: ignore[arg-type]
    try:
        configure_logging(settings)
        configure_logging(settings)
        added = [h for h in root.handlers if h not in before]
        assert len(added) == 2
        assert all(isinstance(h.formatter, JsonFormatter) for h in added)
        assert sum(isinstance(f, TailSampler) for f in WIDE.filters) == 1
        assert not logging.getLogger("uvicorn.access").propagate
        assert logging.getLogger("uvicorn.error").propagate
    finally:
        for h in [h for h in root.handlers if h not in before]:
            root.removeHandler(h)
            h.close()


def test_get_logger_prefix() -> None:
    assert get_logger("game").name == "botpit.game"
    assert get_logger("botpit.game").name == "botpit.game"
    assert get_logger("botpit").name == "botpit"
