"""Formula parity: every computed cell against docs/spec.md §14.2 (73 cells).

Each function mirrors one Excel formula class; the parameterized cases are the
workbook's cached results, so a failing id names the exact cell that drifted.
"""

from __future__ import annotations

import math

import pytest

from otp_tracker import calculations as C
from otp_tracker.models import Leg, PositionBlock, Portfolio, RollLedger, RollPair, VisibleQuote
from tests.conftest import GOLDEN


def _assert_cell(actual, expected, cell: str) -> None:
    assert actual is not None, f"{cell}: computed None"
    assert math.isclose(
        float(actual), float(expected), rel_tol=1e-8, abs_tol=1e-9
    ), f"{cell}: actual={actual!r} expected={expected!r}"


@pytest.mark.parametrize("cell,expected", sorted(GOLDEN.items()), ids=sorted(GOLDEN))
def test_golden_cell_from_seed(portfolio, cell, expected):
    values = C.render_values(portfolio)
    assert cell in values, f"{cell} missing from render_values()"
    _assert_cell(values[cell], expected, cell)


def test_render_values_keys_are_exactly_the_populated_cells(portfolio):
    values = C.render_values(portfolio)
    expected = set(GOLDEN) | {"P40", "R40"}
    assert set(values) == expected
    # U is absent for the three rows that have no EV cell in the workbook.
    assert not {"U11", "U12", "U13"} & set(values)


# --------------------------------------------------------------------------- #
# Individual leg formulas (H, L, M, P, R, U)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("row", [5, 6, 7, 8, 9, 10, 11, 12, 13])
def test_leg_formulas_match_golden(portfolio, row):
    leg = next(item for item in portfolio.all_legs() if item.sheet_row == row)
    _assert_cell(C.leg_entry_value(leg), GOLDEN[f"H{row}"], f"H{row}")
    _assert_cell(C.leg_mark_value(leg), GOLDEN[f"L{row}"], f"L{row}")
    _assert_cell(C.leg_pl_open(leg), GOLDEN[f"M{row}"], f"M{row}")
    _assert_cell(C.leg_total_delta(leg), GOLDEN[f"P{row}"], f"P{row}")
    _assert_cell(C.leg_total_theta(leg), GOLDEN[f"R{row}"], f"R{row}")
    if leg.has_ev_column:
        _assert_cell(C.leg_total_ev(leg), GOLDEN[f"U{row}"], f"U{row}")


@pytest.mark.parametrize("last_row", [6, 8, 10, 13])
def test_block_formulas_match_golden(portfolio, last_row):
    block = next(item for item in portfolio.blocks if item.row_end == last_row)
    _assert_cell(C.block_net_entry(block), GOLDEN[f"G{last_row}"], f"G{last_row}")
    _assert_cell(C.block_net_mark(block), GOLDEN[f"K{last_row}"], f"K{last_row}")
    _assert_cell(C.block_rolling_pl(block), GOLDEN[f"X{last_row}"], f"X{last_row}")
    _assert_cell(C.block_total_pl(block), GOLDEN[f"Y{last_row}"], f"Y{last_row}")
    _assert_cell(C.block_roi(block), GOLDEN[f"Z{last_row}"], f"Z{last_row}")


def test_portfolio_totals_match_golden(portfolio):
    _assert_cell(C.portfolio_total_delta(portfolio.blocks), 36.4, "P40")
    _assert_cell(C.portfolio_total_theta(portfolio.blocks), 9.2, "R40")
    _assert_cell(C.portfolio_open_pl(portfolio.blocks), -4675.0, "sum(M)")
    _assert_cell(C.portfolio_total_pl(portfolio.blocks), -4626.0, "sum(Y)")
    _assert_cell(C.portfolio_capital(portfolio.blocks), 6663.0, "sum(I)")


# --------------------------------------------------------------------------- #
# Guard rails / negative cases (spec §6, §14.3)
# --------------------------------------------------------------------------- #
def _leg(**kwargs) -> Leg:
    defaults = dict(sheet_row=5, symbol=None, entry_date=None, qty=1.0, entry_price=1.0)
    defaults.update(kwargs)
    return Leg(**defaults)


def test_block_net_entry_empty_block_returns_none():
    assert C.block_net_entry(PositionBlock(row_start=5, row_end=5)) is None


def test_block_net_mark_empty_block_returns_none():
    assert C.block_net_mark(PositionBlock(row_start=5, row_end=5)) is None


def test_block_net_entry_zero_qty_returns_none_instead_of_raising():
    block = PositionBlock(row_start=5, row_end=5, legs=[_leg(qty=0.0)])
    assert C.block_net_entry(block) is None


def test_block_net_mark_zero_divisor_returns_none_instead_of_raising():
    block = PositionBlock(row_start=5, row_end=5, legs=[_leg(qty=0.0)])
    assert C.block_net_mark(block) is None
    block.net_divisor = 0.0
    assert C.block_net_mark(block) is None


def test_block_roi_none_and_zero_capital_return_none():
    block = PositionBlock(row_start=5, row_end=5, capital=None, legs=[_leg()])
    assert C.block_roi(block) is None
    block.capital = 0.0
    assert C.block_roi(block) is None


def test_blank_entry_price_counts_as_zero_but_stays_none_in_model():
    leg = _leg(entry_price=None, qty=-100.0)
    assert C.leg_entry_value(leg) == 0.0
    assert math.copysign(1.0, C.leg_entry_value(leg)) > 0  # never -0.0
    assert leg.entry_price is None


def test_negative_qty_flips_every_leg_sign():
    long_leg = _leg(qty=2.0, entry_price=3.0, visible=VisibleQuote(mark_price=4.0, delta=0.5, theta=-0.2, ev=1.0))
    short_leg = _leg(qty=-2.0, entry_price=3.0, visible=VisibleQuote(mark_price=4.0, delta=0.5, theta=-0.2, ev=1.0))
    assert C.leg_entry_value(long_leg) == 6.0
    assert C.leg_entry_value(short_leg) == -6.0
    assert C.leg_mark_value(long_leg) == 8.0
    assert C.leg_mark_value(short_leg) == -8.0
    assert C.leg_pl_open(long_leg) == 2.0
    assert C.leg_pl_open(short_leg) == -2.0
    assert C.leg_total_delta(long_leg) == 1.0
    assert C.leg_total_delta(short_leg) == -1.0
    assert C.leg_total_theta(long_leg) == pytest.approx(-0.4)
    assert C.leg_total_theta(short_leg) == pytest.approx(0.4)
    assert C.leg_total_ev(long_leg) == 2.0
    assert C.leg_total_ev(short_leg) == -2.0


def test_net_divisor_override_is_used_and_flagged():
    block = PositionBlock(
        row_start=11,
        row_end=13,
        capital=425.0,
        net_divisor=1000.0,
        roll=RollLedger(literal_zero=True),
        legs=[_leg(sheet_row=13, qty=500.0, entry_price=1.53, visible=VisibleQuote(mark_price=1.17))],
    )
    assert C.block_net_mark(block) == pytest.approx(585.0 / 1000.0)
    assert C.net_divisor_overridden(block) is True


def test_net_divisor_equal_to_qty_is_not_flagged():
    block = PositionBlock(row_start=5, row_end=6, net_divisor=100.0, legs=[_leg(sheet_row=6, qty=100.0)])
    assert C.net_divisor_overridden(block) is False


def test_net_divisor_absent_is_never_flagged():
    block = PositionBlock(row_start=5, row_end=6, legs=[_leg(sheet_row=6, qty=100.0)])
    assert C.net_divisor_overridden(block) is False


def test_rolling_pl_uses_credits_and_pairs_times_100():
    block = PositionBlock(
        row_start=5,
        row_end=6,
        roll=RollLedger(pairs=[RollPair(2.0, 1.5)], credits=[0.25]),
        legs=[_leg(sheet_row=6, qty=1.0)],
    )
    assert C.block_rolling_pl(block) == pytest.approx(75.0)
    assert C.block_total_pl(block) == pytest.approx(-1.0 + 75.0)


# --------------------------------------------------------------------------- #
# Recompute-after-mutation: the UI's core promise
# --------------------------------------------------------------------------- #
def test_mutating_a_mark_recomputes_the_dependent_cells(portfolio):
    block = portfolio.blocks[1]  # rows 7-8
    leg8 = next(item for item in block.legs if item.sheet_row == 8)
    leg8.visible.mark_price = 3.00  # was 2.46

    assert C.leg_mark_value(leg8) == pytest.approx(300.0)
    assert C.leg_pl_open(leg8) == pytest.approx(300.0 - 2947.0)
    assert C.block_total_pl(block) == pytest.approx(-2647.0 + 51.0 + 511.0)
    assert C.block_roi(block) == pytest.approx((-2647.0 + 51.0 + 511.0) / 2550.0)
    assert C.render_values(portfolio)["Y8"] == pytest.approx(-2085.0)


def test_mutating_qty_recomputes_entry_and_mark_values(portfolio):
    leg8 = portfolio.blocks[1].legs[1]
    leg8.qty = 50.0
    assert C.leg_entry_value(leg8) == pytest.approx(29.47 * 50)
    assert C.leg_mark_value(leg8) == pytest.approx(2.46 * 50)
    assert C.portfolio_total_delta(portfolio.blocks) == pytest.approx(36.4 - 22.8 + 0.228 * 50)


def test_render_values_rounds_to_nine_decimals_are_stable(portfolio):
    values = C.render_values(portfolio)
    for cell, expected in GOLDEN.items():
        assert round(float(values[cell]), 9) == round(float(expected), 9), cell


def test_empty_portfolio_renders_only_zero_totals():
    values = C.render_values(Portfolio())
    assert values == {"P40": 0, "R40": 0}
