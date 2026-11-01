"""Headless UI + entry-point smoke test (no display, no browser required).

Run from the project root:

    .venv/bin/python scripts/verify_ui_smoke.py

Only ``ft.Page`` is stubbed (``update``/``show_dialog``/``run_task``); every widget
comes from the app's own builders, so a failure here is a real defect.  Covers:

  * shell builds, rail navigation renders Positions / Summary / Ledger
  * Positions grid: 9 rows, 26 columns, Excel-formatted totals strip
  * market-data toggle rebuilds columns (Flet pitfall A)
  * add / edit / delete through the real dialogs, with live recomputation
  * dialog validation keeps the dialog open and adds no leg
  * quote JSON application, roll-ledger literal parse/render round trip
  * save -> reload persistence
  * entry point main(): healthy, missing workbook, corrupt workbook, no source
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="otp_ui_smoke_"))
os.environ["OTP_TRACKER_STORE"] = str(TMP / "store.json")
os.environ.setdefault("OTP_TRACKER_XLSX", str(Path.home() / "Downloads" / "OTPTracker.xlsx"))

import flet as ft  # noqa: E402

from otp_tracker import calculations as C  # noqa: E402
from otp_tracker import store as store_mod  # noqa: E402
from otp_tracker.app import build_app  # noqa: E402
from otp_tracker.app_state import init_state  # noqa: E402
from otp_tracker.components.position_dialog import open_position_dialog  # noqa: E402
from otp_tracker.components.quote_dialog import apply_quote_array, open_quote_dialog  # noqa: E402
from otp_tracker.components.roll_dialog import open_roll_dialog  # noqa: E402
from otp_tracker.models import RollLedger  # noqa: E402
from otp_tracker.store import DataSourceError, bootstrap  # noqa: E402
from otp_tracker.views.ledger_view import build_ledger_view  # noqa: E402
from otp_tracker.views.summary_view import build_summary_view  # noqa: E402

failures: list[str] = []
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)


def check(label: str, actual, expected) -> None:
    if actual != expected:
        failures.append(f"{label}: actual={actual!r} expected={expected!r}")


def close_to(label: str, actual, expected, tol: float = 1e-9) -> None:
    try:
        ok = math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=tol)
    except (TypeError, ValueError):
        ok = False
    if not ok:
        failures.append(f"{label}: actual={actual!r} expected≈{expected!r}")


class StubPage:
    """Duck-typed ``ft.Page`` — records what the app hands it."""

    def __init__(self) -> None:
        self.web = False
        self.title = ""
        self.theme_mode = None
        self.padding = None
        self.updates = 0
        self.dialogs: list = []
        self.controls: list = []
        self.window = type(
            "W", (), {"prevent_close": False, "on_event": None,
                      "min_width": 0, "min_height": 0, "destroy": lambda self=None: None},
        )()

    def update(self, *args, **kwargs) -> None:
        self.updates += 1

    def show_dialog(self, dialog) -> None:
        dialog.open = True
        self.dialogs.append(dialog)
        self.update()

    def close_dialog(self, *args, **kwargs) -> None:
        pass

    def run_task(self, handler, *args, **kwargs) -> None:
        coro = handler() if callable(handler) else handler
        if asyncio.iscoroutine(coro):
            LOOP.run_until_complete(coro)

    def run_thread(self, handler, *args, **kwargs) -> None:
        handler(*args, **kwargs)

    def add(self, *controls) -> None:
        self.controls.extend(controls)


def walk(control, *, seen=None):
    """Yield every control reachable from ``control`` (dataclass-safe, cycle-safe)."""
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
            except Exception:  # noqa: BLE001
                continue
            if isinstance(value, (ft.Control, list, tuple, dict)) or hasattr(value, "__dataclass_fields__"):
                yield from walk(value, seen=seen)
    elif isinstance(control, ft.Control):
        yield control


def texts(root) -> list[str]:
    return [c.value for c in walk(root)
            if isinstance(c, ft.Text) and isinstance(c.value, str)]


def by_key(root, key):
    for control in walk(root):
        if getattr(control, "key", None) == key:
            return control
    return None


# =========================================================== 1. shell + grid
boot = bootstrap()
check("boot_origin", boot.origin, "xlsx")
page = StubPage()
state = init_state(boot, page)
app = build_app(page)                 # binds state.grid
grid = state.grid

check("shell_is_control", isinstance(app, ft.Control), True)
check("nav_present", by_key(app, "main-nav") is not None, True)
check("grid_rows", len(grid.rows), 9)
check("grid_columns", len(grid.columns), 26)
check("grid_sticky_header", grid.fixed_top_rows, 1)
check("grid_fixed_left", grid.fixed_left_columns, 3)

shell_texts = texts(app)
check("title_verbatim", "OTP PORFOLIO TRACKER - OPEN TRADES" in shell_texts, True)
check("banner_entry", "Entry Data" in shell_texts, True)
check("banner_market", "Market Data" in shell_texts, True)
check("as_of_annotation", "As of 12/18/2021" in shell_texts, True)
check("total_delta", by_key(app, "total-delta").value, "36.40")
check("total_theta", by_key(app, "total-theta").value, "9.20")
check("total_pl", by_key(app, "total-pl").value, "(4,626.00)")
check("total_capital", by_key(app, "total-capital").value, "6,663.00")
check("grid_cell_excel_style", "(1,170.00)" in " ".join(shell_texts), True)

# ================================================= 2. market-data column toggle
state.show_market = False
state.refresh("market hidden")
check("columns_hidden", len(grid.columns), 16)
check("rows_intact_when_hidden", len(grid.rows), 9)
state.show_market = True
state.refresh("market shown")
check("columns_shown", len(grid.columns), 26)

# ============================================================= 3. add/edit/del
rows_before = len(grid.rows)
dialog = open_position_dialog(page, state, mode="add_leg")
check("add_dialog_open", dialog.open, True)
for key, value in {
    "f-symbol": ".SPY220617C400",
    "f-date": "3/1/2022",
    "f-qty": "-2",
    "f-entry-price": "5.5",
    "f-capital": "1100",
}.items():
    by_key(dialog, key).value = value
by_key(dialog, "save-position").on_click(None)
check("add_dialog_closed_declaratively", dialog.open, False)
check("rows_after_add", len(grid.rows), rows_before + 1)
check("legs_after_add", len(state.legs()), 10)
new_leg = state.legs()[-1]
check("new_leg_qty", new_leg.qty, -2.0)
check("new_leg_price", new_leg.entry_price, 5.5)
check("new_leg_symbol", new_leg.symbol, ".SPY220617C400")
check("new_leg_date", new_leg.entry_date.isoformat(), "2022-03-01")
# H = -11 with no mark => M = +11, so portfolio Total P/L rises by 11
check("total_pl_after_add", by_key(app, "total-pl").value, "(4,615.00)")

edit = open_position_dialog(page, state, mode="edit", leg=new_leg)
by_key(edit, "f-qty").value = "-4"
by_key(edit, "save-position").on_click(None)
check("edit_applied", new_leg.qty, -4.0)
check("edit_dialog_closed", edit.open, False)
check("total_pl_after_edit", by_key(app, "total-pl").value, "(4,604.00)")

bad = open_position_dialog(page, state, mode="edit", leg=new_leg)
by_key(bad, "f-symbol").value = "NOTASYMBOL"
by_key(bad, "save-position").on_click(None)
check("invalid_symbol_keeps_dialog", bad.open, True)
check("invalid_symbol_inline_error", bool(by_key(bad, "f-symbol").error), True)
check("invalid_symbol_no_leg_added", len(state.legs()), 10)
by_key(bad, "cancel-position").on_click(None)
check("cancel_closes_dialog", bad.open, False)

state.remove_leg(new_leg)
state.refresh()
check("legs_after_delete", len(state.legs()), 9)
check("rows_after_delete", len(grid.rows), 9)
check("total_pl_after_delete", by_key(app, "total-pl").value, "(4,626.00)")

# ================================================================= 4. dialogs
quote = open_quote_dialog(page, state, state.legs()[0])
check("quote_dialog_open", quote.open, True)
quote.open = False

applied, warnings = apply_quote_array(state.legs(), [{
    "ticker": "DIS", "info": "DIS Mar 18 2022 145 Call", "itm": False, "dte": 11,
    "underlyingPrice": 136.48, "markPrice": 1.08, "extrinsicValue": "1.080",
    "priceChange": -1.17, "delta": 0.206, "gamma": 0.03, "theta": -0.118, "vega": 0.07,
}])
check("quote_applied", applied, 1)
check("quote_no_warnings", warnings, [])
check("quote_mark", state.legs()[0].visible.mark_price, 1.08)
check("quote_not_placeholder", state.legs()[0].quote.is_placeholder, False)

block = state.blocks()[0]
roll = open_roll_dialog(page, state, block)
check("roll_dialog_open", roll.open, True)
roll.open = False
close_to("roll_value", C.block_rolling_pl(block), -650.0)
literal = block.roll.render()
check("roll_literal_shape", literal.startswith("=((") and literal.endswith(")*100"), True)
close_to("roll_render_parse_roundtrip", RollLedger.from_expression(literal).value(), block.roll.value())
parsed = RollLedger.from_expression("=((1.34-1.55)+1.07)*100")
check("roll_parse_pairs", [(p.new_px, p.closed_px) for p in parsed.pairs], [(1.34, 1.55)])
check("roll_parse_credits", parsed.credits, [1.07])
close_to("roll_parse_value", parsed.value(), 86.0)
check("roll_parse_literal_zero", RollLedger.from_expression("0").literal_zero, True)

# ============================================================== 5. other views
summary = build_summary_view(page)
summary_texts = texts(summary)
check("summary_title", "Portfolio Summary" in summary_texts, True)
check("summary_delta_card", by_key(summary, "sum-total-delta").value, "36.40")
check("summary_theta_card", by_key(summary, "sum-total-theta").value, "9.20")
check("summary_divisor_warning", "1000" in " ".join(summary_texts), True)
check("summary_placeholder_warning", "placeholder" in " ".join(summary_texts).lower(), True)
check("summary_block_row", by_key(summary, "block-11") is not None, True)

ledger_view = build_ledger_view(page)
ledger_texts = " ".join(texts(ledger_view))
check("ledger_title", "Monthly Realized P/L" in ledger_texts, True)
check("ledger_feb", "Feb 2022" in ledger_texts, True)
check("ledger_mar", "Mar 2022" in ledger_texts, True)
check("ledger_nke", "(354.00)" in ledger_texts, True)
check("ledger_extra_column", "-680" in ledger_texts, True)
check("ledger_month_key", by_key(ledger_view, "month-2022-02") is not None, True)

nav = by_key(app, "main-nav")
for index, marker in ((1, "Portfolio Summary"), (2, "Monthly Realized P/L"),
                      (0, "OTP PORFOLIO TRACKER - OPEN TRADES")):
    nav.selected_index = index
    nav.on_change(type("E", (), {"control": nav})())
    check(f"nav_{index}", marker in texts(app), True)

# ============================================================== 6. persistence
state.changed_since_save = True
state.save_now()
check("store_file_written", Path(os.environ["OTP_TRACKER_STORE"]).exists(), True)
reloaded = bootstrap()
check("reload_origin", reloaded.origin, "store")
check("reload_legs", len(reloaded.portfolio.all_legs()), len(state.legs()))
close_to("reload_total_delta", C.portfolio_total_delta(reloaded.portfolio.blocks), 36.4)

# ========================================================= 7. entry point main()
from main import main as entry  # noqa: E402


def run_entry() -> StubPage:
    entry_page = StubPage()
    entry(entry_page)
    return entry_page


def joined(entry_page: StubPage) -> str:
    return " ".join(texts(entry_page.controls))


# 7a. healthy workbook (fresh store path so the workbook is the source)
os.environ["OTP_TRACKER_STORE"] = str(TMP / "entry.json")
os.environ["OTP_TRACKER_XLSX"] = str(Path.home() / "Downloads" / "OTPTracker.xlsx")
healthy = run_entry()
check("main_title", healthy.title, "OTP Tracker")
check("main_renders_grid", "OTP PORFOLIO TRACKER - OPEN TRADES" in joined(healthy), True)
check("main_status_reports_import", "Imported" in joined(healthy) and "9 legs / 4 blocks" in joined(healthy), True)
check("main_totals", "36.40" in joined(healthy) and "9.20" in joined(healthy), True)

# 7b. missing workbook -> bundled seed + explanation
os.environ["OTP_TRACKER_STORE"] = str(TMP / "missing.json")
os.environ["OTP_TRACKER_XLSX"] = str(TMP / "absent.xlsx")
missing = run_entry()
check("main_missing_starts", "OTP PORFOLIO TRACKER - OPEN TRADES" in joined(missing), True)
check("main_missing_message",
      "No workbook at" in joined(missing) and "bundled sample data loaded" in joined(missing), True)

# 7c. corrupt workbook -> warning + seed fallback
corrupt_path = TMP / "corrupt.xlsx"
corrupt_path.write_bytes(b"not a zip file")
os.environ["OTP_TRACKER_STORE"] = str(TMP / "corrupt.json")
os.environ["OTP_TRACKER_XLSX"] = str(corrupt_path)
corrupt = run_entry()
check("main_corrupt_starts", "OTP PORFOLIO TRACKER - OPEN TRADES" in joined(corrupt), True)
check("main_corrupt_warning", "Could not import" in joined(corrupt), True)

# 7d. every source unusable -> fatal screen with remedies
os.environ["OTP_TRACKER_STORE"] = str(TMP / "empty.json")
os.environ["OTP_TRACKER_XLSX"] = str(TMP / "still-absent.xlsx")
original_load_seed = store_mod.load_seed


def _broken_seed(*args, **kwargs):
    raise DataSourceError("seed damaged")


store_mod.load_seed = _broken_seed
try:
    fatal = run_entry()
finally:
    store_mod.load_seed = original_load_seed
check("main_fatal_title", "No data source found" in joined(fatal), True)
check("main_fatal_remedies", "OTP_TRACKER_XLSX" in joined(fatal), True)

# ===================================================================== report
print(f"ui smoke assertions: {len(failures)} failures")
if failures:
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print("HEADLESS UI + ENTRY-POINT SMOKE: ALL CHECKS PASS")
