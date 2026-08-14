"""Standalone checks for welkom auth field-chain resolution.

Run with ``uv run python tests/check_resolution.py``. Not a pytest module: the
integration is content-in-root (an ``__init__.py`` at the repo root), which makes
pytest try to import the whole package — and Home Assistant — just to collect a
test here. ``const.py`` has no Home Assistant or relative imports, so it is
loaded directly by path and its pure resolver exercised in isolation.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_CONST_PATH = pathlib.Path(__file__).resolve().parent.parent / "const.py"
_spec = importlib.util.spec_from_file_location("welkom_const", _CONST_PATH)
assert _spec and _spec.loader
const = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(const)

resolve = const.resolve_mapped_user_id
PERSON = const.WELKOM_FIELD_HEADERS["person"]
ROLE = const.WELKOM_FIELD_HEADERS["role"]

CONFIG = {
    const.CONF_PERSON_USERS: {"douwe": "user-douwe-owner", "belem": "user-belem"},
    const.CONF_ROLE_USERS: {
        "parent": "user-parent-shared",
        "staff": "user-staff",
        "resident": "user-resident",
    },
    const.CONF_DEFAULT_USER: "user-default",
}

# name, config, headers, person_trusted, expected
CASES: list[tuple[str, dict, dict, bool, str | None]] = [
    (
        "trusted person wins over role",
        CONFIG,
        {PERSON: "douwe", ROLE: "admin"},
        True,
        "user-douwe-owner",
    ),
    (
        "unmapped person -> role",
        CONFIG,
        {PERSON: "araceli", ROLE: "staff"},
        True,
        "user-staff",
    ),
    ("no person header -> role", CONFIG, {ROLE: "resident"}, True, "user-resident"),
    (
        "nothing mapped -> default",
        CONFIG,
        {PERSON: "ghost", ROLE: "x"},
        True,
        "user-default",
    ),
    ("no headers -> default", CONFIG, {}, True, "user-default"),
    (
        "blank person value ignored",
        CONFIG,
        {PERSON: "  ", ROLE: "staff"},
        True,
        "user-staff",
    ),
    (
        "no match, no default -> None",
        {
            const.CONF_PERSON_USERS: {},
            const.CONF_ROLE_USERS: {},
            const.CONF_DEFAULT_USER: "",
        },
        {PERSON: "ghost", ROLE: "x"},
        True,
        None,
    ),
    # Security gate: a downgraded person (capped below their assigned role) is
    # NOT trusted, so person map is skipped and resolution falls to the role.
    # Douwe (admin) spoofed onto residents (capped parent) -> shared Parent
    # account, never his owner account.
    (
        "downgraded person -> role account, not owner",
        CONFIG,
        {PERSON: "douwe", ROLE: "parent"},
        False,
        "user-parent-shared",
    ),
    (
        "downgraded person, no role map -> default",
        CONFIG,
        {PERSON: "douwe", ROLE: "visitor"},
        False,
        "user-default",
    ),
]


def main() -> int:
    """Run every case; return 0 on success, 1 on the first failure."""
    failed = 0
    for name, config, headers, person_trusted, expected in CASES:
        got = resolve(config, headers, person_trusted)
        ok = got == expected
        print(f"{'PASS' if ok else 'FAIL'}: {name} -> {got!r}")
        if not ok:
            print(f"      expected {expected!r}")
            failed += 1
    print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
