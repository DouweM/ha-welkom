"""Standalone checks for `heard.py`: when a person's devices were last heard.

Run with ``uv run python tests/check_heard.py``. Not a pytest module, for the
same reason as ``check_changes.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
import pathlib
import sys
import types
from types import SimpleNamespace
from typing import Any

_ROOT = pathlib.Path(__file__).resolve().parent.parent

_pkg = types.ModuleType("welkom")
_pkg.__path__ = [str(_ROOT)]
sys.modules["welkom"] = _pkg


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"welkom.{name}", _ROOT / f"{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"welkom.{name}"] = module
    spec.loader.exec_module(module)
    return module


_load("const")
models = _load("models")
heard = _load("heard")

T0 = datetime(2026, 10, 19, 22, 54, 56, tzinfo=UTC)


def conn(person: str | None, home: str | None, seen: datetime | None) -> Any:
    """Just what `last_heard` reads off a connection."""
    return SimpleNamespace(
        person=SimpleNamespace(id=person) if person else None,
        home=SimpleNamespace(id=home) if home else None,
        metadata=models.Metadata(last_seen=seen),
    )


def check_newest_per_person_at_the_home() -> None:
    got = heard.last_heard(
        [
            conn("reynaldo", "oasis", T0),
            conn("reynaldo", "oasis", T0 + timedelta(seconds=40)),  # the watch
            conn("reynaldo", "awaysys", T0 + timedelta(hours=1)),  # elsewhere
            conn("araceli", "oasis", None),
            conn(None, "oasis", T0 + timedelta(hours=2)),  # nobody's
        ],
        "oasis",
    )
    assert got == {"reynaldo": T0 + timedelta(seconds=40)}, got


def check_naive_is_utc() -> None:
    naive = T0.replace(tzinfo=None)
    got = heard.last_heard([conn("belem", "oasis", naive)], "oasis")
    assert got["belem"] == T0, got


def check_a_stay_keeps_its_newest_and_survives_the_off() -> None:
    h = heard.HeardWhileHome()
    assert h.update(False, None) is None  # nothing since a restart
    assert h.update(True, T0) == T0
    assert h.update(True, T0 + timedelta(seconds=85)) == T0 + timedelta(seconds=85)
    # The phone drops out of the list before welkom lets go of the person: an
    # older reading from another device must not move it back.
    assert h.update(True, T0) == T0 + timedelta(seconds=85)
    assert h.update(True, None) == T0 + timedelta(seconds=85)
    # Off: kept, whatever the data says now.
    assert h.update(False, None) == T0 + timedelta(seconds=85)
    assert h.update(False, T0 + timedelta(hours=1)) == T0 + timedelta(seconds=85)


def check_a_new_stay_starts_over() -> None:
    h = heard.HeardWhileHome()
    h.update(True, T0 + timedelta(hours=1))
    h.update(False, None)
    # Back on with a reading older than the last stay's (a clock that went
    # backwards, a cached value): the new stay's own reading wins.
    assert h.update(True, T0) == T0
    h.update(False, None)
    # Back on with nothing heard yet: nothing, not the last stay's time.
    assert h.update(True, None) is None


for name, check in list(globals().items()):
    if name.startswith("check_") and callable(check):
        check()
        print(f"ok {name}")
