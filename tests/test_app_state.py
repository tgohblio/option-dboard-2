"""AppState contracts: mutations, totals, status, persistence (spec §11.6)."""

from __future__ import annotations

import asyncio
import json

import pytest

from otp_tracker import app_state as app_state_mod
from otp_tracker import calculations as C
from otp_tracker.app_state import AppState, get_state, init_state, _boot_status
from otp_tracker.models import Leg, Portfolio, PositionBlock, RollLedger
from otp_tracker.store import BootResult, Store, bootstrap


def test_get_state_before_init_raises():
    app_state_mod._STATE = None
    with pytest.raises(RuntimeError, match="not initialised"):
        get_state()


def test_boot_status_xlsx_reports_source_and_counts():
    boot = BootResult(
        portfolio=Portfolio(blocks=[PositionBlock(row_start=5, row_end=5)]),
        ledger=app_state_mod.MonthlyLedger(),
        store=Store(),
        source={"file": "/tmp/OTPTracker.xlsx"},
        origin="xlsx",
    )
    status = _boot_status(boot)
    assert "Imported /tmp/OTPTracker.xlsx" in status
    assert "legs / 1 blocks" in status


def test_boot_status_seed_explains_missing_workbook(monkeypatch, tmp_path):
    monkeypatch.setenv("OTP_TRACKER_XLSX", str(tmp_path / "none.xlsx"))
    boot = BootResult(portfolio=Portfolio(), ledger=app_state_mod.MonthlyLedger(), store=Store(), origin="seed")
    status = _boot_status(boot)
    assert "No workbook at" in status
    assert "bundled sample data loaded" in status
    assert "Import .xlsx" in status


def test_boot_status_store_and_warnings():
    boot = BootResult(
        portfolio=Portfolio(),
        ledger=app_state_mod.MonthlyLedger(),
        store=Store(),
        origin="store",
        warnings=["first", "second", "third"],
    )
    status = _boot_status(boot)
    assert "Loaded store" in status
    assert "first" in status and "second" in status
    assert "third" not in status  # capped at two warnings


def test_init_state_copies_warnings_and_sets_status(boot, stub_page):
    boot.warnings = ["w"]
    state = init_state(boot, stub_page)
    try:
        assert state.warnings == ["w"]
        assert isinstance(state.status, str) and state.status
        assert state.page is stub_page
    finally:
        app_state_mod._STATE = None


def test_totals_and_counts(state):
    current, _ = state
    assert current.total_delta() == pytest.approx(36.4)
    assert current.total_theta() == pytest.approx(9.2)
    assert current.open_pl() == pytest.approx(-4675.0)
    assert current.total_pl() == pytest.approx(-4626.0)
    assert current.total_capital() == pytest.approx(6663.0)
    assert len(current.legs()) == 9
    assert len(current.blocks()) == 4


def test_block_of_boundaries(state):
    current, _ = state
    leg5 = current.legs()[0]
    leg7 = next(leg for leg in current.legs() if leg.sheet_row == 7)
    leg13 = current.legs()[-1]
    assert current.block_of(leg5).row_start == 5
    assert current.block_of(leg7).row_start == 7
    assert current.block_of(leg13).row_start == 11


def test_append_leg_extends_the_last_block(state):
    current, _ = state
    block = current.blocks()[-1]
    new_leg = current.new_leg(symbol=".SPY220617C400", qty=-2.0, entry_price=5.5)
    assert new_leg.sheet_row == 14
    current.append_leg(new_leg)
    assert block.row_end == 14
    assert block.legs[-1] is new_leg
    assert current.blocks()[-1] is block


def test_append_leg_to_explicit_block(state):
    current, _ = state
    block = current.blocks()[0]
    leg = current.new_leg(sheet_row=14, qty=1.0)
    current.append_leg(leg, block)
    assert block.row_end == 14
    assert block.legs[-1] is leg


def test_append_leg_creates_the_first_block_when_empty(stub_page):
    from otp_tracker.store import BootResult

    state = AppState(portfolio=Portfolio(), ledger=app_state_mod.MonthlyLedger(), store=Store())
    leg = Leg(sheet_row=5, symbol=None, entry_date=None, qty=1.0, entry_price=None)
    block = state.append_leg(leg)
    assert block.row_start == 5 and block.row_end == 5
    assert state.blocks() == [block]


def test_remove_leg_shrinks_the_block(state):
    current, _ = state
    block = current.blocks()[3]
    current.remove_leg(current.legs()[-1])  # row 13
    assert block.row_end == 12
    assert [leg.sheet_row for leg in block.legs] == [11, 12]


def test_remove_last_leg_removes_the_block(state):
    current, _ = state
    for row in (12, 13):
        current.remove_leg(next(leg for leg in current.legs() if leg.sheet_row == row))
    assert len(current.blocks()) == 4
    current.remove_leg(next(leg for leg in current.legs() if leg.sheet_row == 11))
    assert len(current.blocks()) == 3
    assert current.blocks()[-1].row_end == 10


def test_remove_leg_clears_selection_and_ignores_unknown_leg(state):
    current, _ = state
    leg = current.legs()[0]
    current.selected_leg = leg
    current.remove_leg(leg)
    assert current.selected_leg is None
    stranger = Leg(sheet_row=999, symbol=None, entry_date=None, qty=1, entry_price=None)
    before = len(current.legs())
    current.remove_leg(stranger)
    assert len(current.legs()) == before


def test_new_leg_defaults(state):
    current, _ = state
    leg = current.new_leg()
    assert leg.sheet_row == 14
    assert leg.symbol is None and leg.entry_price is None and leg.qty == 0.0


def test_payload_contains_portfolio_and_ledger(state, seed_payload):
    current, _ = state
    payload = current.payload()
    assert payload["version"] == 1
    assert len(payload["blocks"]) == 4
    assert payload["ledger"] == seed_payload["ledger"]


def test_refresh_rebinds_grid_and_totals(state, stub_page):
    current, _ = state
    from otp_tracker.views.positions_view import build_positions_view

    root = build_positions_view(stub_page)
    assert current.grid is not None
    assert len(current.grid.rows) == 9
    assert current.status_text.value == current.status

    current.show_market = False
    current.refresh("changed")
    assert len(current.grid.columns) == 16
    assert len(current.grid.rows) == 9
    assert current.status_text.value == "changed"
    assert stub_page.updates > 0
    assert current.totals["delta"].value == "36.40"
    assert current.totals["theta"].value == "9.20"
    assert current.totals["pl"].value == "(4,626.00)"
    assert current.totals["capital"].value == "6,663.00"


def test_refresh_without_bindings_is_safe(state):
    current, _ = state
    current.grid = None
    current.status_text = None
    current.totals = {}
    current.refresh("no bindings")
    assert current.status == "no bindings"


def test_touch_marks_dirty_and_increments_revision(state):
    current, _ = state
    before = current._revision
    current.touch("dirty")
    assert current.changed_since_save is True
    assert current._revision == before + 1
    assert current.status == "dirty"


def test_save_async_writes_and_clears_dirty(state):
    current, page = state
    current.changed_since_save = True
    ok = asyncio.run(current.save_async())
    assert ok is True
    assert current.changed_since_save is False
    assert current.store.path.is_file()
    assert current.status.startswith("Saved to")
    payload = json.loads(current.store.path.read_text(encoding="utf-8"))
    assert len(payload["blocks"]) == 4


def test_save_async_reports_io_failure(state, monkeypatch):
    current, page = state

    def boom(payload):
        raise OSError("disk full")

    monkeypatch.setattr(current.store, "save_payload", boom)
    ok = asyncio.run(current.save_async())
    assert ok is False
    assert "Save failed" in current.status


def test_save_now_returns_false_on_failure(state, monkeypatch):
    current, _ = state

    def boom(payload):
        raise OSError("read-only")

    monkeypatch.setattr(current.store, "save_payload", boom)
    assert current.save_now() is False


def test_debounced_autosave_persists_changes(state, monkeypatch, tmp_path):
    current, page = state
    monkeypatch.setattr(app_state_mod, "_AUTOSAVE_DELAY", 0)
    current.page = None  # exercise the save path without scheduling a task
    current.changed_since_save = True
    asyncio.run(current._debounced_save())
    assert current.store.path.is_file()
    assert current.changed_since_save is False


def test_persistence_round_trip_after_mutation(state, tmp_path):
    current, _ = state
    leg = current.new_leg(symbol=".SPY220617C400", qty=-2.0, entry_price=5.5)
    current.append_leg(leg)
    current.save_now()

    reloaded = bootstrap(store=Store(tmp_path / "store.json"), xlsx_path=tmp_path / "absent.xlsx")
    assert reloaded.origin == "store"
    assert len(reloaded.portfolio.all_legs()) == 10
    assert C.portfolio_total_delta(reloaded.portfolio.blocks) == pytest.approx(current.total_delta())
    assert C.portfolio_total_pl(reloaded.portfolio.blocks) == pytest.approx(current.total_pl())
