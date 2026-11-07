"""Shared fixtures and helpers for the OTP Tracker test suite.

Ground rules encoded here:

* ``src/`` is put on ``sys.path`` exactly like ``flet run`` does.
* Every test gets a **fresh deep copy** of the bundled seed portfolio, so a
  mutation in one test can never leak into another.
* Every test gets its own ``Store`` path under ``tmp_path`` — the suite never
  touches ``data/app_store.json`` or the user's workbook.
* ``StubPage`` is the only fake: it duck-types the handful of ``ft.Page``
  members the app uses.  Every widget comes from the app's own builders, so a
  failure here is a real defect, not a mock artefact.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from otp_tracker import app_state as app_state_mod  # noqa: E402
from otp_tracker import store as store_mod  # noqa: E402
from otp_tracker.store import BootResult, Store, ledger_from_payload, portfolio_from_payload  # noqa: E402

WORKBOOK = Path.home() / "Downloads" / "OTPTracker.xlsx"
SEED_FILE = SRC / "otp_tracker" / "seed_otp.json"
FIXTURE_FILE = ROOT / "data" / "seed_fixture.json"

#: docs/spec.md §14.2 — every cached computed cell of the real workbook.
GOLDEN: dict[str, float] = {
    # per-leg H/L/M/P/R/U
    "H5": 0, "L5": -108, "M5": -108, "P5": -20.6, "R5": 11.8, "U5": -108,
    "H6": 0, "L6": 755, "M6": 755, "P6": 56.3, "R6": -8.8, "U6": 605,
    "H7": -102, "L7": -51, "M7": 51, "P7": -15.9, "R7": 8.8, "U7": -51,
    "H8": 2947, "L8": 246, "M8": -2701, "P8": 22.8, "R8": -3.1, "U8": 246,
    "H9": -82, "L9": -246, "M9": -164, "P9": -22.8, "R9": 3.1, "U9": -246,
    "H10": 2200, "L10": 117, "M10": -2083, "P10": 16.6, "R10": -2.6, "U10": 117,
    "H11": 1640, "L11": 585, "M11": -1055, "P11": 83, "R11": -13,
    "H12": -1980, "L12": -1170, "M12": 810, "P12": -166, "R12": 26,
    "H13": 765, "L13": 585, "M13": -180, "P13": 83, "R13": -13,
    # block-level
    "G6": 0, "K6": 6.47, "X6": -650, "Y6": -3, "Z6": -0.001819284415,
    "G8": 28.45, "K8": 1.95, "X8": 511, "Y8": -2139, "Z8": -0.8388235294,
    "G10": 21.18, "K10": -1.29, "X10": 188, "Y10": -2059, "Z10": -1.00980873,
    "G13": 0.85, "K13": 0, "X13": 0, "Y13": -425, "Z13": -1,
    # totals
    "P40": 36.4, "R40": 9.2,
}

EXPECTED_INFO = {
    5: "DIS Mar 18 2022 145 Call",
    6: "DIS Apr 14 2022 135 Call (Weekly)",
    7: "AMD Jul 15 2022 85 Call",
    8: "AMD Oct 21 2022 100 Call",
    10: "JPM Sep 16 2022 130 Call",
}
PLACEHOLDER_ROWS = (9, 11, 12, 13)
EXPECTED_PURPLE = {
    9: {"L", "M"},
    10: {"L", "M", "Y", "Z"},
    11: {"L", "M", "P", "R"},
    12: {"L", "M", "P", "R"},
    13: {"L", "M", "P", "R", "Z"},
}
EXPECTED_X_LITERALS = {
    6: "=((1.34-1.55)+(2.46-3)+(1.16-2.1)+(2.7-2.63)+(1.09-0.25)+(1.61-0.7)+(1.21-0.09)+(1.43-5.43)+(3.63-3.44)+(2.93-8.23)+(1.21-0.01)+(0.74-0.3)+(3.35-5.8)+(1.8-0.7)+1.07)*100",
    8: "=((1.72-1.8)+(2.35-0.07)+(1.17-0.06)+(1.81-0.03)+(0.68-0.03)+(1.59-2.51)+(1.81-0.03)+(2.65-11.23)+(3.73-0.46)+(1.19-2.84)+(2.1-2.19)+(3.3-0.02)+(0.35-0.03)+(0.96-0.02)+1.02)*100",
    10: "=((1.61-2.2)+(1.12-3.94)+(1.46-0.13)+(1.14-0.13)+(3.11-4.79)+(0.71-0.1)+(1.71-0.02)+(1.28-3.28)+(2.98-2.88)+(3.28-0.02)+(0.95-2.68)+(1.98-0.1)+0.82)*100",
    13: "0",
}


class StubWindow(SimpleNamespace):
    def __init__(self) -> None:
        super().__init__()
        self.prevent_close = False
        self.on_event = None
        self.min_width = 0
        self.min_height = 0
        self.destroyed = 0

    def destroy(self) -> None:
        self.destroyed += 1


class StubPage:
    """Duck-typed ``ft.Page``: records calls instead of talking to a Flutter client.

    ``run_task`` deliberately parks coroutines instead of awaiting them: the
    app schedules debounced autosaves with a 500 ms sleep, and running those in
    every widget test would make the suite slow and timing-sensitive.  Tests
    that need a save call :meth:`otp_tracker.app_state.AppState.save_now` (the
    blocking shutdown path) or run the coroutine explicitly.
    """

    def __init__(self) -> None:
        self.web = False
        self.title = ""
        self.theme_mode = None
        self.padding = None
        self.updates = 0
        self.dialogs: list = []
        self.controls: list = []
        self.pending: list = []
        self.window = StubWindow()

    def update(self, *args, **kwargs) -> None:
        self.updates += 1

    def show_dialog(self, dialog) -> None:
        dialog.open = True
        self.dialogs.append(dialog)
        self.update()

    def pop_dialog(self, *args, **kwargs) -> None:
        if self.dialogs:
            self.dialogs[-1].open = False
        self.update()

    def close_dialog(self, *args, **kwargs) -> None:
        self.update()

    def run_task(self, handler, *args, **kwargs) -> None:
        result = handler(*args, **kwargs) if callable(handler) else handler
        if asyncio.iscoroutine(result):
            self.pending.append(result)

    def run_thread(self, handler, *args, **kwargs):
        return handler(*args, **kwargs)

    def add(self, *controls) -> None:
        self.controls.extend(controls)

    def close_pending(self) -> None:
        for coro in self.pending:
            coro.close()
        self.pending.clear()


def walk(control, *, seen=None):
    """Yield every control reachable from ``control`` (dataclass-safe, cycle-safe)."""
    from flet import Control

    if control is None or isinstance(control, (str, int, float, bool, bytes)):
        return
    seen = seen if seen is not None else set()
    if isinstance(control, (list, tuple, set, frozenset)):
        for item in control:
            yield from walk(item, seen=seen)
        return
    if isinstance(control, dict):
        for item in control.values():
            yield from walk(item, seen=seen)
        return
    if id(control) in seen:
        return
    seen.add(id(control))
    if hasattr(control, "__dataclass_fields__"):
        yield control
        for name in control.__dataclass_fields__:
            if name.startswith("_"):
                continue
            try:
                value = getattr(control, name)
            except Exception:  # noqa: BLE001 - Flet properties may raise before mount
                continue
            if isinstance(value, (Control, list, tuple, dict)) or hasattr(value, "__dataclass_fields__"):
                yield from walk(value, seen=seen)
    elif isinstance(control, Control):
        yield control


def by_key(root, key):
    for control in walk(root):
        if getattr(control, "key", None) == key:
            return control
    return None


def by_keys(root, key):
    return [control for control in walk(root) if getattr(control, "key", None) == key]


def texts(root) -> list[str]:
    from flet import Text

    return [c.value for c in walk(root) if isinstance(c, Text) and isinstance(c.value, str)]


def joined_text(root) -> str:
    return " ".join(texts(root))


def button(root, key):
    control = by_key(root, key)
    assert control is not None, f"control with key {key!r} not found"
    return control


def click(root, key) -> None:
    button(root, key).on_click(None)


def load_seed_payload() -> dict:
    return json.loads(SEED_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def seed_payload() -> dict:
    return load_seed_payload()


@pytest.fixture()
def portfolio(seed_payload):
    return portfolio_from_payload(seed_payload)


@pytest.fixture()
def ledger(seed_payload):
    return ledger_from_payload(seed_payload.get("ledger"))


@pytest.fixture()
def boot(tmp_path, portfolio, ledger, seed_payload):
    return BootResult(
        portfolio=portfolio,
        ledger=ledger,
        store=Store(tmp_path / "store.json"),
        source=seed_payload.get("source"),
        origin="seed",
    )


@pytest.fixture()
def stub_page() -> StubPage:
    return StubPage()


@pytest.fixture()
def state(boot, stub_page):
    """A live AppState bound to a stub page and a throwaway store path."""
    app_state_mod._STATE = None
    current = app_state_mod.init_state(boot, stub_page)
    try:
        yield current, stub_page
    finally:
        stub_page.close_pending()
        app_state_mod._STATE = None


@pytest.fixture()
def app_session(state):
    """AppState + the fully built shell (which binds ``state.grid``)."""
    current, page = state
    from otp_tracker.app import build_app

    root = build_app(page)
    return current, page, root


@pytest.fixture()
def workbook_path() -> Path:
    if not WORKBOOK.is_file():
        pytest.skip(f"reference workbook not present: {WORKBOOK}")
    return WORKBOOK
