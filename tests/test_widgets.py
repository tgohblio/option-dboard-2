"""Headless widget/workflow tests (spec §14.3 ``test_widgets.py``).

Every control is produced by the app's own builders; only ``ft.Page`` is stubbed.
Covers the shell, the 26-column grid, the market-data toggle, dialog-driven
CRUD with live recomputation, validation, quotes, roll ledgers, the summary and
ledger screens, persistence, and the ``main()`` entry point.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import flet as ft
import pytest

from otp_tracker import app_state as app_state_mod
from otp_tracker import calculations as C
from otp_tracker.components.position_dialog import open_position_dialog
from otp_tracker.components.quote_dialog import (
    apply_quote_array,
    apply_quote_to_leg,
    open_quote_dialog,
)
from otp_tracker.components.roll_dialog import open_roll_dialog
from otp_tracker.models import QuoteSnapshot
from otp_tracker.store import Store, bootstrap
from otp_tracker.views.ledger_view import build_ledger_view
from otp_tracker.views.positions_view import build_positions_view
from otp_tracker.views.summary_view import build_summary_view
from tests.conftest import button, by_key, click, joined_text, texts, walk

VALID_QUOTE = {
    "ticker": "DIS",
    "info": "DIS Mar 18 2022 145 Call",
    "itm": False,
    "dte": 11,
    "underlyingPrice": 136.48,
    "markPrice": 2.0,
    "extrinsicValue": "2.000",
    "priceChange": -0.25,
    "delta": 0.206,
    "gamma": 0.03,
    "theta": -0.118,
    "vega": 0.07,
}


# =========================================================================== #
# Shell and positions grid
# =========================================================================== #
def test_shell_has_rail_and_positions_grid(app_session):
    state, page, root = app_session
    assert by_key(root, "main-nav") is not None
    assert by_key(root, "otp-table") is state.grid
    assert state.grid.fixed_top_rows == 1
    assert state.grid.fixed_left_columns == 3
    assert len(state.grid.rows) == 9
    assert len(state.grid.columns) == 26


def test_title_band_is_verbatim_with_as_of(app_session):
    state, page, root = app_session
    rendered = texts(root)
    assert "OTP PORFOLIO TRACKER - OPEN TRADES" in rendered  # typo preserved
    assert "Entry Data" in rendered
    assert "Market Data" in rendered
    assert "As of 12/18/2021" in rendered


def test_row_keys_and_cell_counts(app_session):
    state, page, root = app_session
    keys = [row.key for row in state.grid.rows]
    assert keys == [f"row-{row}" for row in range(5, 14)]
    assert all(len(row.cells) == 26 for row in state.grid.rows)


def test_totals_strip_matches_excel(app_session):
    state, page, root = app_session
    assert by_key(root, "total-delta").value == "36.40"
    assert by_key(root, "total-theta").value == "9.20"
    assert by_key(root, "total-pl").value == "(4,626.00)"
    assert by_key(root, "total-capital").value == "6,663.00"


def test_grid_keeps_excel_display_style(app_session):
    state, page, root = app_session
    rendered = joined_text(root)
    assert "(1,170.00)" in rendered      # L12, parentheses + thousands separator
    assert "-100.98%" in rendered       # Z10, below -100%, never clamped
    assert "6,663.00" in rendered
    # Rows 11-13 have a blank entry date, which Excel shows as an empty cell;
    # the grid renders None as the documented em dash.
    assert "3/17/2022" in rendered


def test_seed_boot_status_explains_data_source(app_session):
    state, page, root = app_session
    assert "bundled sample data loaded" in state.status
    assert "9 legs / 4 blocks" in state.status
    assert by_key(root, "status-strip").value == state.status


def test_market_toggle_hides_n_through_w_and_keeps_rows(app_session):
    state, page, root = app_session
    click(root, "toggle-market")
    assert state.show_market is False
    assert len(state.grid.columns) == 16
    assert len(state.grid.rows) == 9
    assert button(root, "toggle-market").content == "Show market data (N–W)"

    click(root, "toggle-market")
    assert state.show_market is True
    assert len(state.grid.columns) == 26


def test_rail_navigation_swaps_all_three_views(app_session):
    state, page, root = app_session
    nav = by_key(root, "main-nav")
    for index, marker in ((1, "Portfolio Summary"), (2, "Monthly Realized P/L"),
                          (0, "OTP PORFOLIO TRACKER - OPEN TRADES")):
        nav.selected_index = index
        nav.on_change(SimpleNamespace(control=nav))
        assert marker in texts(root), f"route {index}"


def test_market_toggle_survives_navigation(app_session):
    state, page, root = app_session
    click(root, "toggle-market")
    nav = by_key(root, "main-nav")
    nav.selected_index = 1
    nav.on_change(SimpleNamespace(control=nav))
    state.navigate("/")
    assert len(state.grid.columns) == 16


# =========================================================================== #
# Add / edit / delete through the real dialogs
# =========================================================================== #
def _fill(dialog, values: dict) -> None:
    for key, value in values.items():
        by_key(dialog, key).value = value


def test_add_leg_appends_recomputes_and_keeps_the_row_rendered(app_session):
    """Pitfall-B regression: closing the dialog must not drop the appended row."""
    state, page, root = app_session
    rows_before = len(state.grid.rows)
    dialog = open_position_dialog(page, state, mode="add_leg")
    assert dialog.open is True
    _fill(dialog, {
        "f-symbol": ".SPY220617C400",
        "f-date": "3/1/2022",
        "f-qty": "-2",
        "f-entry-price": "5.5",
        "f-capital": "1100",
    })
    click(dialog, "save-position")

    assert dialog.open is False
    assert len(state.grid.rows) == rows_before + 1
    assert "row-14" in [row.key for row in state.grid.rows]
    assert len(state.legs()) == 10
    leg = state.legs()[-1]
    assert leg.sheet_row == 14
    assert leg.symbol == ".SPY220617C400"
    assert leg.entry_date.isoformat() == "2022-03-01"
    assert leg.qty == -2.0 and leg.entry_price == 5.5
    assert state.block_of(leg).row_end == 14
    assert by_key(root, "total-pl").value == "(4,615.00)"
    assert state.status == "Added leg at row 14"


def test_add_block_creates_a_new_strategy_block(app_session):
    state, page, root = app_session
    dialog = open_position_dialog(page, state, mode="add_block")
    _fill(dialog, {
        "f-strategy": "BWB",
        "f-symbol": ".QQQ220617P300",
        "f-qty": "2",
        "f-entry-price": "3",
        "f-capital": "600",
    })
    click(dialog, "save-position")

    assert len(state.blocks()) == 5
    block = state.blocks()[-1]
    assert (block.row_start, block.row_end) == (14, 14)
    assert block.strategy == "BWB"
    assert block.capital == 600.0
    assert len(state.grid.rows) == 10
    assert "row-14" in [row.key for row in state.grid.rows]
    assert "BWB" in joined_text(root)
    assert state.status == "Added block at row 14"


def test_edit_leg_recomputes_dependents(app_session):
    state, page, root = app_session
    leg = next(item for item in state.legs() if item.sheet_row == 8)
    before_total = state.total_pl()
    dialog = open_position_dialog(page, state, mode="edit", leg=leg)
    _fill(dialog, {"f-qty": "50", "f-entry-price": "29.47"})
    click(dialog, "save-position")

    assert dialog.open is False
    assert leg.qty == 50.0
    assert C.leg_entry_value(leg) == pytest.approx(1473.5)
    assert C.leg_mark_value(leg) == pytest.approx(123.0)
    assert state.total_pl() == pytest.approx(before_total + 1350.5)
    assert state.status == "Position updated"


def test_edit_last_leg_updates_capital_and_divisor(app_session):
    state, page, root = app_session
    block = state.blocks()[3]
    leg13 = block.legs[-1]
    dialog = open_position_dialog(page, state, mode="edit", leg=leg13)
    assert by_key(dialog, "f-capital").value == "425"
    assert by_key(dialog, "f-net-divisor").value == "1000"
    _fill(dialog, {"f-capital": "900", "f-net-divisor": "500"})
    click(dialog, "save-position")

    assert block.capital == 900.0
    assert block.net_divisor == 500.0
    assert C.net_divisor_overridden(block) is False


def test_edit_cancel_leaves_the_model_untouched(app_session):
    state, page, root = app_session
    leg = next(item for item in state.legs() if item.sheet_row == 7)
    dialog = open_position_dialog(page, state, mode="edit", leg=leg)
    _fill(dialog, {"f-qty": "999"})
    click(dialog, "cancel-position")
    assert dialog.open is False
    assert leg.qty == -100.0


@pytest.mark.xfail(
    strict=True,
    reason="DEF-3: spec §11.4 says capital/net-divisor are shown only for a last-row edit, "
    "but the dialog renders them for every row and silently ignores them on save",
)
def test_non_last_row_edit_does_not_offer_block_level_fields(app_session):
    state, page, root = app_session
    leg7 = next(item for item in state.legs() if item.sheet_row == 7)
    dialog = open_position_dialog(page, state, mode="edit", leg=leg7)
    capital_field = by_key(dialog, "f-capital")
    assert capital_field is None or capital_field.disabled is True


def test_delete_requires_a_selection(app_session):
    state, page, root = app_session
    state.selected_leg = None
    dialogs_before = len(page.dialogs)
    click(root, "delete-row")
    assert len(page.dialogs) == dialogs_before
    assert "Select a row first" in state.status


def test_delete_with_confirmation_removes_the_leg(app_session):
    state, page, root = app_session
    leg5 = state.legs()[0]
    state.selected_leg = leg5
    click(root, "delete-row")
    confirm = page.dialogs[-1]
    assert "Delete position" in joined_text(confirm)
    click(confirm, "confirm-ok")

    assert len(state.legs()) == 8
    assert len(state.grid.rows) == 8
    assert leg5 not in state.legs()
    assert "row-5" not in [row.key for row in state.grid.rows]
    assert state.status == "Deleted row 5"


def test_delete_cancel_keeps_the_leg(app_session):
    state, page, root = app_session
    state.selected_leg = state.legs()[0]
    click(root, "delete-row")
    confirm = page.dialogs[-1]
    cancel = next(
        control
        for control in walk(confirm)
        if isinstance(control, ft.TextButton) and control.content == "Cancel"
    )
    cancel.on_click(None)
    assert confirm.open is False
    assert len(state.legs()) == 9


def test_deleting_the_last_leg_removes_the_block(app_session):
    state, page, root = app_session
    for row in (12, 13):
        state.remove_leg(next(leg for leg in state.legs() if leg.sheet_row == row))
    state.selected_leg = next(leg for leg in state.legs() if leg.sheet_row == 11)
    click(root, "delete-row")
    click(page.dialogs[-1], "confirm-ok")

    assert len(state.blocks()) == 3
    assert len(state.legs()) == 6
    assert len(state.grid.rows) == 6


# =========================================================================== #
# Dialog validation (defensive / negative paths)
# =========================================================================== #
@pytest.mark.parametrize(
    "field,value,error_fragment,on_field",
    [
        ("f-symbol", "NOTASYMBOL", "Unrecognised option symbol", True),
        ("f-symbol", "AMD221332C85", "Invalid expiry date", True),
        ("f-qty", "", "Qty is required", False),
        ("f-qty", "abc", "Qty must be a number", False),
        ("f-entry-price", "abc", "Entry price must be a number", False),
        ("f-date", "13/40/2022", "Entry date must look like", False),
        ("f-date", "2022-02-30", "Entry date must look like", False),
        ("f-capital", "abc", "Block capital must be a number", False),
    ],
)
def test_position_dialog_validation_keeps_dialog_open(app_session, field, value, error_fragment, on_field):
    state, page, root = app_session
    before = len(state.legs())
    dialog = open_position_dialog(page, state, mode="add_leg")
    _fill(dialog, {"f-symbol": ".AMD220715C85", "f-qty": "1", "f-entry-price": "1"})
    by_key(dialog, field).value = value
    click(dialog, "save-position")

    assert dialog.open is True
    assert len(state.legs()) == before
    if on_field:
        assert error_fragment in (by_key(dialog, field).error or "")
    else:
        assert error_fragment in joined_text(dialog)


def test_position_dialog_allows_blank_optional_fields(app_session):
    state, page, root = app_session
    dialog = open_position_dialog(page, state, mode="add_leg")
    _fill(dialog, {"f-qty": "1"})
    click(dialog, "save-position")
    leg = state.legs()[-1]
    assert dialog.open is False
    assert leg.symbol is None
    assert leg.entry_price is None
    assert leg.entry_date is None
    assert leg.qty == 1.0


# =========================================================================== #
# Quote dialog
# =========================================================================== #
def test_quote_dialog_apply_object_updates_marks_and_totals(app_session):
    state, page, root = app_session
    leg5 = state.legs()[0]
    dialog = open_quote_dialog(page, state, leg5)
    assert "DIS Mar 18 2022 145 Call" in joined_text(dialog)
    by_key(dialog, "quote-paste").value = json.dumps(VALID_QUOTE)
    click(dialog, "quote-apply")

    assert dialog.open is False
    assert leg5.quote.is_placeholder is False
    assert leg5.quote.extrinsic_value == 2.0
    assert leg5.visible.mark_price == 2.0
    assert C.leg_mark_value(leg5) == pytest.approx(-200.0)
    assert state.total_pl() == pytest.approx(-4718.0)
    assert by_key(root, "total-pl").value == "(4,718.00)"
    assert "Quote refreshed for row 5" in state.status


def test_quote_dialog_placeholder_never_overwrites_visible_marks(app_session):
    state, page, root = app_session
    leg5 = state.legs()[0]
    original_mark = leg5.visible.mark_price
    dialog = open_quote_dialog(page, state, leg5)
    by_key(dialog, "quote-paste").value = json.dumps(
        {"ticker": "None", "info": "None", "markPrice": 0, "delta": 0, "theta": 0}
    )
    click(dialog, "quote-apply")

    assert leg5.visible.mark_price == original_mark
    assert leg5.quote.is_placeholder is True
    assert "placeholder" in state.status


def test_quote_dialog_invalid_json_keeps_dialog_open(app_session):
    state, page, root = app_session
    dialog = open_quote_dialog(page, state, state.legs()[0])
    by_key(dialog, "quote-paste").value = "{not-json"
    click(dialog, "quote-apply")
    assert dialog.open is True
    assert "Invalid JSON" in joined_text(dialog)


def test_quote_dialog_rejects_scalar_payload(app_session):
    state, page, root = app_session
    dialog = open_quote_dialog(page, state, state.legs()[0])
    by_key(dialog, "quote-paste").value = "42"
    click(dialog, "quote-apply")
    assert dialog.open is True
    assert "object or an array" in joined_text(dialog)


def test_quote_dialog_empty_paste_closes_without_change(app_session):
    state, page, root = app_session
    leg = state.legs()[0]
    before = leg.visible
    dialog = open_quote_dialog(page, state, leg)
    click(dialog, "quote-apply")
    assert dialog.open is False
    assert leg.visible == before


def test_quote_array_matching_by_ticker_and_info(app_session):
    state, page, root = app_session
    applied, warnings = apply_quote_array(state.legs(), [VALID_QUOTE])
    assert (applied, warnings) == (1, [])
    assert state.legs()[0].visible.mark_price == 2.0


def test_quote_array_unique_ticker_fallback(app_session):
    state, page, root = app_session
    applied, warnings = apply_quote_array(
        state.legs(),
        [{"ticker": "JPM", "info": "JPM unmatched label", "markPrice": 3.0, "delta": 0.1, "theta": -0.1}],
    )
    assert (applied, warnings) == (1, [])
    jpm = next(leg for leg in state.legs() if leg.sheet_row == 10)
    assert jpm.visible.mark_price == 3.0


def test_quote_array_ambiguous_ticker_is_not_applied(app_session):
    state, page, root = app_session
    applied, warnings = apply_quote_array(
        state.legs(),
        [{"ticker": "AMD", "info": "AMD unmatched label", "markPrice": 9.0}],
    )
    assert applied == 0
    assert any("No leg matched" in warning for warning in warnings)


def test_quote_array_unknown_ticker_warns(app_session):
    state, page, root = app_session
    applied, warnings = apply_quote_array(
        state.legs(), [{"ticker": "ZZZ", "info": "nope", "markPrice": 1.0}]
    )
    assert applied == 0
    assert any("ZZZ" in warning for warning in warnings)


def test_quote_array_skips_non_objects(app_session):
    state, page, root = app_session
    applied, warnings = apply_quote_array(state.legs(), [1, "x", None])
    assert applied == 0
    assert len(warnings) == 3


def test_apply_quote_to_leg_marks_placeholder_without_visible_change(app_session):
    state, page, root = app_session
    leg = state.legs()[0]
    before = leg.visible
    apply_quote_to_leg(leg, QuoteSnapshot())  # all-zero placeholder
    assert leg.quote.is_placeholder is True
    assert leg.visible == before


# =========================================================================== #
# Roll ledger dialog
# =========================================================================== #
def test_roll_dialog_opens_with_the_real_ledger(app_session):
    state, page, root = app_session
    block = state.blocks()[0]
    dialog = open_roll_dialog(page, state, block)
    assert "rows 5–6" in joined_text(dialog)
    assert "-650.00" in joined_text(dialog)  # preview of X6


def test_roll_dialog_save_replaces_credits_and_recomputes(app_session):
    state, page, root = app_session
    block = state.blocks()[0]
    dialog = open_roll_dialog(page, state, block)
    by_key(dialog, "roll-credits").value = "2"
    click(dialog, "roll-save")

    assert dialog.open is False
    assert block.roll.credits == [2.0]
    assert C.block_rolling_pl(block) == pytest.approx(-557.0)
    assert "-557.00" in joined_text(root)


def test_roll_dialog_parses_an_excel_expression(app_session):
    state, page, root = app_session
    block = state.blocks()[0]
    dialog = open_roll_dialog(page, state, block)
    by_key(dialog, "roll-paste").value = "=((1.34-1.55)+1.07)*100"
    click(dialog, "roll-parse")
    assert "86.00" in joined_text(dialog)
    click(dialog, "roll-save")

    assert [(p.new_px, p.closed_px) for p in block.roll.pairs] == [(1.34, 1.55)]
    assert block.roll.credits == [1.07]
    assert C.block_rolling_pl(block) == pytest.approx(86.0)
    assert "86.00" in joined_text(root)


def test_roll_dialog_rejects_an_incomplete_pair(app_session):
    state, page, root = app_session
    block = state.blocks()[0]
    dialog = open_roll_dialog(page, state, block)
    click(dialog, "roll-add")
    new_fields = [c for c in walk(dialog) if isinstance(c, ft.TextField) and c.label == "New"]
    new_fields[-1].value = "2.5"  # no closed price on the new row
    click(dialog, "roll-save")

    assert dialog.open is True
    assert "needs both prices" in joined_text(dialog)


def test_roll_dialog_literal_zero(app_session):
    state, page, root = app_session
    block = state.blocks()[0]
    dialog = open_roll_dialog(page, state, block)
    checkbox = next(
        control
        for control in walk(dialog)
        if isinstance(control, ft.Checkbox) and "literal 0" in control.label
    )
    checkbox.value = True
    click(dialog, "roll-save")
    assert block.roll.literal_zero is True
    assert C.block_rolling_pl(block) == 0.0


# =========================================================================== #
# Summary view
# =========================================================================== #
def test_summary_cards_match_portfolio_totals(app_session):
    state, page, root = app_session
    summary = build_summary_view(page)
    assert by_key(summary, "sum-total-delta").value == "36.40"
    assert by_key(summary, "sum-total-theta").value == "9.20"
    assert by_key(summary, "sum-open-pl").value == "(4,675.00)"
    assert by_key(summary, "sum-total-pl").value == "(4,626.00)"
    assert by_key(summary, "sum-capital").value == "6,663.00"
    assert by_key(summary, "sum-counts").value == "9 / 4"


def test_summary_has_one_row_per_block(app_session):
    state, page, root = app_session
    summary = build_summary_view(page)
    assert by_key(summary, "block-5") is not None
    assert by_key(summary, "block-7") is not None
    assert by_key(summary, "block-9") is not None
    assert by_key(summary, "block-11") is not None
    rendered = joined_text(summary)
    assert "Diagonal" in rendered and "BWB" in rendered
    assert "(2,139.00)" in rendered  # block 2 total P/L
    assert "-83.88%" in rendered     # block 2 ROI


def test_summary_surfaces_every_documented_warning(app_session):
    state, page, root = app_session
    summary = build_summary_view(page)
    rendered = joined_text(summary)
    assert "Net MARK divisor overridden" in rendered
    assert "1000" in rendered and "500" in rendered
    assert "Placeholder quotes" in rendered
    assert "9, 11, 12, 13" in rendered
    assert "below −100%" in rendered


def test_summary_back_button_navigates_home(app_session):
    state, page, root = app_session
    summary = build_summary_view(page)
    click(summary, "back-to-positions")
    assert len(state.grid.columns) in (16, 26)


# =========================================================================== #
# Ledger view
# =========================================================================== #
def test_ledger_shows_months_entries_and_totals(app_session):
    state, page, root = app_session
    view = build_ledger_view(page)
    rendered = joined_text(view)
    assert "Monthly Realized P/L" in rendered
    assert "Feb 2022" in rendered and "Mar 2022" in rendered
    assert "(354.00)" in rendered          # NKE
    assert "-680" in rendered              # GLD extra column
    assert by_key(view, "month-2022-02") is not None
    assert by_key(view, "month-2022-03") is not None
    assert by_key(view, "entry-NKE") is not None
    assert by_key(view, "entry-GLD") is not None
    assert "Feb 2022: (3,476.00)" in by_key(view, "ledger-totals").value
    assert "Mar 2022: (265.00)" in by_key(view, "ledger-totals").value


def test_ledger_month_total_excludes_the_extra_column(app_session):
    state, page, root = app_session
    view = build_ledger_view(page)
    assert "Extra excluded" in by_key(view, "ledger-totals").value
    assert "-680" not in by_key(view, "ledger-totals").value


def test_ledger_add_month(app_session):
    state, page, root = app_session
    view = build_ledger_view(page)
    click(view, "add-month")
    dialog = page.dialogs[-1]
    by_key(dialog, "month-field").value = "2022-04"
    click(dialog, "save-month")
    assert dialog.open is False
    assert [m.month.isoformat() for m in state.ledger.months] == [
        "2022-02-01", "2022-03-01", "2022-04-01"
    ]
    assert by_key(view, "month-2022-04") is not None
    assert state.status == "Added Apr 2022"


@pytest.mark.parametrize("text,fragment", [("2022-02", "already exists"), ("nope", "Month must look like"), ("2022-13", "Month must look like")])
def test_ledger_add_month_validation(app_session, text, fragment):
    state, page, root = app_session
    view = build_ledger_view(page)
    before = len(state.ledger.months)
    click(view, "add-month")
    dialog = page.dialogs[-1]
    by_key(dialog, "month-field").value = text
    click(dialog, "save-month")
    assert dialog.open is True
    assert fragment in joined_text(dialog)
    assert len(state.ledger.months) == before


def test_ledger_add_entry(app_session):
    state, page, root = app_session
    view = build_ledger_view(page)
    click(view, "add-entry")
    dialog = page.dialogs[-1]
    _fill(dialog, {"entry-ticker": "aapl", "entry-pnl": "-100", "entry-extra": ""})
    click(dialog, "save-entry")

    assert dialog.open is False
    march = state.ledger.months[1]
    assert (march.entries[-1].ticker, march.entries[-1].pnl) == ("AAPL", -100.0)
    assert march.total == pytest.approx(-365.0)
    assert "Mar 2022: (365.00)" in by_key(view, "ledger-totals").value


@pytest.mark.parametrize(
    "ticker,pnl,extra,fragment",
    [
        ("", "-100", "", "Ticker is required"),
        ("AAPL", "", "", "could not convert"),
        ("AAPL", "abc", "", "could not convert"),
        ("AAPL", "-100", "abc", "could not convert"),
    ],
)
def test_ledger_add_entry_validation(app_session, ticker, pnl, extra, fragment):
    state, page, root = app_session
    view = build_ledger_view(page)
    before = len(state.ledger.months[1].entries)
    click(view, "add-entry")
    dialog = page.dialogs[-1]
    _fill(dialog, {"entry-ticker": ticker, "entry-pnl": pnl, "entry-extra": extra})
    click(dialog, "save-entry")
    assert dialog.open is True
    assert len(state.ledger.months[1].entries) == before
    assert fragment in joined_text(dialog)


def test_ledger_delete_entry(app_session):
    state, page, root = app_session
    view = build_ledger_view(page)
    delete_control = next(
        control
        for control in walk(view)
        if isinstance(control, ft.Container)
        and isinstance(getattr(control, "tooltip", None), str)
        and control.tooltip.startswith("Delete GLD")
    )
    delete_control.on_click(None)

    feb = state.ledger.months[0]
    assert [entry.ticker for entry in feb.entries] == ["NKE", "ADBE", "FB", "SPX"]
    assert feb.total == pytest.approx(-3181.0)
    assert "Feb 2022: (3,181.00)" in by_key(view, "ledger-totals").value


# =========================================================================== #
# Persistence round trip through the UI state
# =========================================================================== #
def test_ui_mutation_survives_save_and_reload(app_session, tmp_path):
    state, page, root = app_session
    dialog = open_position_dialog(page, state, mode="add_leg")
    _fill(dialog, {"f-symbol": ".SPY220617C400", "f-qty": "-2", "f-entry-price": "5.5"})
    click(dialog, "save-position")
    expected_delta = state.total_delta()
    expected_pl = state.total_pl()

    assert state.save_now() is True
    reloaded = bootstrap(store=Store(state.store.path), xlsx_path=tmp_path / "absent.xlsx")
    assert reloaded.origin == "store"
    assert len(reloaded.portfolio.all_legs()) == 10
    assert C.portfolio_total_delta(reloaded.portfolio.blocks) == pytest.approx(expected_delta)
    assert C.portfolio_total_pl(reloaded.portfolio.blocks) == pytest.approx(expected_pl)


def test_save_button_writes_the_store(app_session):
    state, page, root = app_session
    click(root, "save")
    assert page.pending, "Save must schedule an async save task"
    asyncio.run(page.pending.pop())
    assert state.store.path.is_file()
    assert state.changed_since_save is False
    assert state.status.startswith("Saved to")
    payload = json.loads(state.store.path.read_text(encoding="utf-8"))
    assert len(payload["blocks"]) == 4


def test_refresh_button_reports_recompute(app_session):
    state, page, root = app_session
    click(root, "refresh")
    assert state.status == "Recomputed 9 legs / 4 blocks in Python"
    assert len(state.grid.rows) == 9


# =========================================================================== #
# main() entry point
# =========================================================================== #
def _run_main(monkeypatch, tmp_path, store_name: str, xlsx_path):
    from main import main as entry

    monkeypatch.setenv("OTP_TRACKER_STORE", str(tmp_path / store_name))
    monkeypatch.setenv("OTP_TRACKER_XLSX", str(xlsx_path))
    page = type("EntryPage", (), {})()
    # reuse the StubPage surface via the fixture class
    from tests.conftest import StubPage

    page = StubPage()
    entry(page)
    return page


def test_main_imports_the_real_workbook(monkeypatch, tmp_path, workbook_path):
    try:
        page = _run_main(monkeypatch, tmp_path, "entry.json", workbook_path)
        rendered = joined_text(page.controls)
        assert page.title == "OTP Tracker"
        assert "OTP PORFOLIO TRACKER - OPEN TRADES" in rendered
        assert "Imported" in rendered and "9 legs / 4 blocks" in rendered
        assert "36.40" in rendered and "9.20" in rendered
        assert by_key(page.controls, "otp-table") is not None
    finally:
        app_state_mod._STATE = None


def test_main_falls_back_to_the_seed_with_a_clear_message(monkeypatch, tmp_path):
    try:
        page = _run_main(monkeypatch, tmp_path, "missing.json", tmp_path / "absent.xlsx")
        rendered = joined_text(page.controls)
        assert "OTP PORFOLIO TRACKER - OPEN TRADES" in rendered
        assert "No workbook at" in rendered
        assert "bundled sample data loaded (9 legs / 4 blocks)" in rendered
    finally:
        app_state_mod._STATE = None


def test_main_warns_on_a_corrupt_workbook_and_still_starts(monkeypatch, tmp_path):
    bad = tmp_path / "corrupt.xlsx"
    bad.write_bytes(b"not a zip")
    try:
        page = _run_main(monkeypatch, tmp_path, "corrupt.json", bad)
        rendered = joined_text(page.controls)
        assert "OTP PORFOLIO TRACKER - OPEN TRADES" in rendered
        assert "Could not import" in rendered
    finally:
        app_state_mod._STATE = None


def test_main_shows_fatal_remedies_when_no_source_exists(monkeypatch, tmp_path):
    from otp_tracker import store as store_mod
    from otp_tracker.store import DataSourceError

    def broken_seed():
        raise DataSourceError("seed damaged")

    monkeypatch.setattr(store_mod, "load_seed", broken_seed)
    try:
        page = _run_main(monkeypatch, tmp_path, "empty.json", tmp_path / "absent.xlsx")
        rendered = joined_text(page.controls)
        assert "No data source found" in rendered
        assert "OTP_TRACKER_XLSX" in rendered
        assert "OTP_TRACKER_STORE" in rendered
    finally:
        app_state_mod._STATE = None


def test_main_never_clobbers_the_import_status_with_a_warning(monkeypatch, tmp_path, workbook_path):
    """Regression: main() used to overwrite the composed status with warnings[0]."""
    try:
        page = _run_main(monkeypatch, tmp_path, "status.json", workbook_path)
        status = by_key(page.controls, "status-strip").value
        assert status.startswith("Imported")
        assert "9 legs / 4 blocks" in status
    finally:
        app_state_mod._STATE = None
