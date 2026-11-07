"""Acceptance gate: the §14.2 golden table and every edge assertion.

Three independent sources must agree with the workbook:

1. ``data/seed_fixture.json`` (the authoritative export) — regenerated through
   ``seed_gen`` and compared to the committed ``seed_otp.json`` to catch drift.
2. the live ``~/Downloads/OTPTracker.xlsx`` (skipped when absent).
3. an export -> re-import round trip of the imported workbook.
"""

from __future__ import annotations

import json
import math

import pytest

from otp_tracker import calculations as C
from otp_tracker import excel_io, formatting, store
from otp_tracker.seed_gen import payload_from_fixture
from tests.conftest import (
    EXPECTED_INFO,
    EXPECTED_PURPLE,
    EXPECTED_X_LITERALS,
    FIXTURE_FILE,
    GOLDEN,
    PLACEHOLDER_ROWS,
)


def assert_golden(portfolio, prefix: str = "") -> None:
    values = C.render_values(portfolio)
    for cell, expected in GOLDEN.items():
        assert cell in values, f"{prefix}{cell} missing"
        assert math.isclose(
            float(values[cell]), float(expected), rel_tol=1e-8, abs_tol=1e-9
        ), f"{prefix}{cell}: actual={values[cell]!r} expected={expected!r}"


def assert_structure(portfolio, ledger, prefix: str = "", check_roll_literals: bool = True) -> None:
    assert len(portfolio.blocks) == 4, prefix
    assert len(portfolio.all_legs()) == 9, prefix
    assert [(b.row_start, b.row_end) for b in portfolio.blocks] == [(5, 6), (7, 8), (9, 10), (11, 13)], prefix
    assert portfolio.as_of_date.isoformat() == "2021-12-18", prefix
    assert portfolio.blocks[3].net_divisor == 1000.0, prefix
    assert C.net_divisor_overridden(portfolio.blocks[3]) is True, prefix
    assert [leg.entry_price for leg in portfolio.blocks[0].legs] == [None, None], prefix
    assert portfolio.blocks[0].legs[0].extra_cells.get("X") == 0.0, prefix
    assert [leg.sheet_row for leg in portfolio.all_legs() if leg.has_ev_column] == [5, 6, 7, 8, 9, 10], prefix
    assert [leg.sheet_row for leg in portfolio.all_legs() if not leg.has_ev_column] == [11, 12, 13], prefix

    by_row = {leg.sheet_row: leg for leg in portfolio.all_legs()}
    for row, info in EXPECTED_INFO.items():
        assert by_row[row].quote.info == info, f"{prefix}row {row}"
        assert by_row[row].quote.is_placeholder is False, f"{prefix}row {row}"
    for row in PLACEHOLDER_ROWS:
        assert by_row[row].quote.is_placeholder is True, f"{prefix}row {row}"
        assert by_row[row].quote.ticker == "None", f"{prefix}row {row}"
    assert by_row[9].visible.mark_price == 2.46, prefix
    for row in (11, 12, 13):
        assert by_row[row].visible.mark_price == 1.17, f"{prefix}row {row}"

    for row, columns in EXPECTED_PURPLE.items():
        assert set(by_row[row].purple_cells) == columns, f"{prefix}row {row}"

    for last_row, literal in EXPECTED_X_LITERALS.items():
        block = next(b for b in portfolio.blocks if b.row_end == last_row)
        if check_roll_literals:
            assert block.roll.render() == literal, f"{prefix}X{last_row}"
        else:
            assert block.roll.value() == pytest.approx(GOLDEN[f"X{last_row}"]), f"{prefix}X{last_row}"

    assert [m.month.isoformat() for m in ledger.months] == ["2022-02-01", "2022-03-01"], prefix
    assert [(e.ticker, e.pnl, e.extra) for e in ledger.months[0].entries] == [
        ("NKE", -354.0, None),
        ("GLD", -295.0, -680.0),
        ("ADBE", -737.0, None),
        ("FB", -1090.0, None),
        ("SPX", -1000.0, None),
    ], prefix
    assert [(e.ticker, e.pnl, e.extra) for e in ledger.months[1].entries] == [("XOP", -265.0, None)], prefix
    assert round(ledger.months[0].total, 6) == -3476.0, prefix
    assert round(ledger.months[1].total, 6) == -265.0, prefix


# --------------------------------------------------------------------------- #
# 1. Fixture (authoritative export) and committed seed
# --------------------------------------------------------------------------- #
def test_fixture_file_is_the_reference_export():
    fixture = json.loads(FIXTURE_FILE.read_text(encoding="utf-8"))
    assert fixture["source"]["sha256"] == "525d60cbfd87cfef2078c8bf068eddecdf632969fd8c148172f5c7519ba525cd"
    assert len(fixture["open_trades"]["data_rows"]) == 10  # rows 5-13 + 40


def test_committed_seed_matches_a_fresh_fixture_regeneration(seed_payload):
    fixture = json.loads(FIXTURE_FILE.read_text(encoding="utf-8"))
    regenerated = payload_from_fixture(fixture)
    committed = json.loads(json.dumps(seed_payload))

    def normalise(payload: dict) -> dict:
        payload = json.loads(json.dumps(payload))
        payload["source"]["imported_at"] = "IGNORED"
        return payload

    assert normalise(committed) == normalise(regenerated)


def test_fixture_golden_table(seed_payload):
    portfolio = store.portfolio_from_payload(seed_payload)
    ledger = store.ledger_from_payload(seed_payload.get("ledger"))
    assert_golden(portfolio, "fixture:")
    assert_structure(portfolio, ledger, "fixture:")


def test_fixture_regeneration_golden_table():
    fixture = json.loads(FIXTURE_FILE.read_text(encoding="utf-8"))
    payload = payload_from_fixture(fixture)
    portfolio = store.portfolio_from_payload(payload)
    ledger = store.ledger_from_payload(payload.get("ledger"))
    assert_golden(portfolio, "regenerated:")
    assert_structure(portfolio, ledger, "regenerated:")


# --------------------------------------------------------------------------- #
# 2. Live workbook
# --------------------------------------------------------------------------- #
def test_live_workbook_golden_table(workbook_path):
    imported = excel_io.import_workbook(workbook_path)
    assert_golden(imported.portfolio, "live:")
    assert_structure(imported.portfolio, imported.ledger, "live:")
    assert imported.portfolio.source_checksum == "525d60cbfd87cfef2078c8bf068eddecdf632969fd8c148172f5c7519ba525cd"


# --------------------------------------------------------------------------- #
# 3. Export -> re-import
# --------------------------------------------------------------------------- #
def test_export_reimport_golden_table(tmp_path, workbook_path):
    imported = excel_io.import_workbook(workbook_path)
    out = excel_io.export_workbook(tmp_path / "roundtrip.xlsx", imported.portfolio, imported.ledger)
    again = excel_io.import_workbook(out)
    assert_golden(again.portfolio, "roundtrip:")
    # Values and literal_zero survive; the structured roll history is DEF-4.
    assert_structure(again.portfolio, again.ledger, "roundtrip:", check_roll_literals=False)


# --------------------------------------------------------------------------- #
# Display parity (§14.1) — rendered text and colour, not just numbers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value,fmt,text,color_attr",
    [
        (755, "money", "755.00", "BLUE"),
        (-650, "money", "(650.00)", "RED"),
        (0, "money", "0.0", "BLACK"),
        (-1.00980873, "percent", "-100.98%", "RED"),
        (-0.001819284415, "percent", "-0.18%", "RED"),
        (-0.8388235294, "percent", "-83.88%", "RED"),
        (-1, "percent", "-100.00%", "RED"),
        (-100, "qty", "(100)", "RED"),
        (36.4, "money", "36.40", "BLUE"),
        (9.2, "money", "9.20", "BLUE"),
        (-4626, "money", "(4,626.00)", "RED"),
        (6663, "money_simple", "6,663.00", "DEFAULT"),
    ],
)
def test_display_parity(value, fmt, text, color_attr):
    display, color = formatting.format_cell(value, fmt)
    assert display == text
    assert color == getattr(formatting, color_attr)


def test_portfolio_totals_render_like_the_workbook_strip(portfolio):
    assert formatting.format_cell(C.portfolio_total_delta(portfolio.blocks), "money")[0] == "36.40"
    assert formatting.format_cell(C.portfolio_total_theta(portfolio.blocks), "money")[0] == "9.20"
    assert formatting.format_cell(C.portfolio_total_pl(portfolio.blocks), "money")[0] == "(4,626.00)"
    assert formatting.format_cell(C.portfolio_capital(portfolio.blocks), "money_simple")[0] == "6,663.00"
