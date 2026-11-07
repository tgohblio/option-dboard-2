"""openpyxl import/export parity (spec §10.3, §14.3).

The real workbook is the primary fixture; synthetic workbooks pin the defensive
paths (bad headers, extra columns, missing sheets, corrupt files) without ever
modifying the user's file.
"""

from __future__ import annotations

import math
from pathlib import Path

import openpyxl
import pytest
from openpyxl import Workbook

from otp_tracker import calculations as C
from otp_tracker import excel_io
from otp_tracker.excel_io import (
    COLUMN_WIDTHS,
    HEADERS,
    WorkbookImportError,
    export_workbook,
    import_workbook,
    sha256_file,
)
from otp_tracker.store import Store, bootstrap
from tests.conftest import EXPECTED_X_LITERALS, GOLDEN


def _write_headers(sheet, extra: dict[str, str] | None = None) -> None:
    for column, label in HEADERS.items():
        sheet[f"{column}4"] = label
    for column, label in (extra or {}).items():
        sheet[f"{column}4"] = label


def _minimal_workbook(path: Path, *, sheet_name: str = "Open Trades", extra_headers=None) -> Path:
    workbook = Workbook()
    sheet = workbook.worksheets[0]
    sheet.title = sheet_name
    _write_headers(sheet, extra_headers)
    sheet["C5"] = ".AMD220715C85"
    sheet["E5"] = -1
    sheet["F5"] = 1.02
    sheet["J5"] = 0.51
    sheet["N5"] = 78.205
    sheet["O5"] = 0.22
    sheet["Q5"] = 0.159
    sheet["S5"] = -0.088
    sheet["T5"] = False
    sheet["U5"] = 0
    sheet["V5"] = 0.51
    sheet["W5"] = 8
    sheet["I5"] = 1649
    sheet["A40"] = "TOTAL"
    sheet["P40"] = -15.9
    sheet["R40"] = 8.8
    workbook.save(path)
    return path


# --------------------------------------------------------------------------- #
# Real workbook import
# --------------------------------------------------------------------------- #
def test_import_real_workbook_structure(workbook_path):
    result = import_workbook(workbook_path)
    assert sha256_file(workbook_path) == "525d60cbfd87cfef2078c8bf068eddecdf632969fd8c148172f5c7519ba525cd"
    assert result.portfolio.source_checksum == "525d60cbfd87cfef2078c8bf068eddecdf632969fd8c148172f5c7519ba525cd"
    assert result.source["file"] == str(workbook_path)
    assert len(result.portfolio.blocks) == 4
    assert len(result.portfolio.all_legs()) == 9
    assert [(b.row_start, b.row_end) for b in result.portfolio.blocks] == [(5, 6), (7, 8), (9, 10), (11, 13)]
    assert result.portfolio.as_of_date.isoformat() == "2021-12-18"


def test_import_real_workbook_warns_once_about_the_k13_override(workbook_path):
    result = import_workbook(workbook_path)
    divisor_warnings = [warning for warning in result.warnings if "divisor override 1000" in warning]
    assert len(divisor_warnings) == 1
    assert "Row 13" in divisor_warnings[0]


def test_import_real_workbook_formula_values(workbook_path):
    result = import_workbook(workbook_path)
    values = C.render_values(result.portfolio)
    for cell, expected in GOLDEN.items():
        assert math.isclose(float(values[cell]), float(expected), rel_tol=1e-8, abs_tol=1e-9), cell


def test_import_real_workbook_edge_metadata(workbook_path):
    result = import_workbook(workbook_path)
    by_row = {leg.sheet_row: leg for leg in result.portfolio.all_legs()}
    assert by_row[5].entry_price is None
    assert by_row[6].entry_price is None
    assert by_row[5].extra_cells == {"X": 0.0}
    assert [row for row, leg in by_row.items() if leg.has_ev_column] == [5, 6, 7, 8, 9, 10]
    assert set(by_row[10].purple_cells) == {"L", "M", "Y", "Z"}
    assert result.portfolio.blocks[3].roll.literal_zero is True
    assert result.ledger.months[0].entries[1].extra == -680.0


def test_import_real_workbook_comments_become_quotes(workbook_path):
    result = import_workbook(workbook_path)
    by_row = {leg.sheet_row: leg for leg in result.portfolio.all_legs()}
    assert by_row[5].quote.info == "DIS Mar 18 2022 145 Call"
    assert by_row[5].quote.extrinsic_value == 1.08
    assert by_row[9].quote.is_placeholder is True
    assert by_row[13].quote.is_placeholder is True


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def test_export_writes_a_faithful_workbook(tmp_path, portfolio, ledger):
    out = tmp_path / "nested" / "export.xlsx"
    written = export_workbook(out, portfolio, ledger)
    assert written == out and out.is_file()

    ws = openpyxl.load_workbook(out)["Open Trades"]
    assert ws["I1"].value == "OTP PORFOLIO TRACKER - OPEN TRADES"
    assert ws["C3"].value == "Entry Data"
    assert ws["K3"].value == "Market Data"
    assert ws.freeze_panes == "A5"
    assert str(ws["A40"].value).strip().upper() == "TOTAL"
    assert ws["P40"].value == pytest.approx(36.4)
    assert ws["R40"].value == pytest.approx(9.2)
    assert ws["U11"].value is None
    assert ws["X5"].value == 0.0  # stray fidelity cell preserved
    assert ws["B5"].value is None
    assert ws["B7"].value == "Diagonal"
    assert ws["B8"].value is None  # strategy only on the block's first row
    assert ws["I8"].value == pytest.approx(2550.0)


def test_export_has_no_formulas(tmp_path, portfolio, ledger):
    out = export_workbook(tmp_path / "export.xlsx", portfolio, ledger)
    ws = openpyxl.load_workbook(out)["Open Trades"]
    formulas = [
        cell.coordinate
        for row in ws.iter_rows()
        for cell in row
        if isinstance(cell.value, str) and cell.value.startswith("=")
    ]
    assert formulas == []


def test_export_preserves_comments(tmp_path, portfolio, ledger):
    out = export_workbook(tmp_path / "export.xlsx", portfolio, ledger)
    ws = openpyxl.load_workbook(out)["Open Trades"]
    assert "ticker" in ws["C5"].comment.text
    assert ws["C13"].comment.text.startswith('{"ticker":"None"')
    assert ws["Y4"].comment.text == "Sum of P/L open + rolling P/L for the trade"
    assert ws["Z4"].comment.text == "Total P/L divided by capital"
    assert ws["K13"].comment.text == "otp-divisor=1000"
    assert ws["X13"].comment.text == "Roll ledger: 0"


def test_export_preserves_layout_metrics(tmp_path, portfolio, ledger):
    out = export_workbook(tmp_path / "export.xlsx", portfolio, ledger)
    ws = openpyxl.load_workbook(out)["Open Trades"]
    for column, width in COLUMN_WIDTHS.items():
        assert ws.column_dimensions[column].width == pytest.approx(width), column
    for column in "NOPQRSTUVW":
        assert ws.column_dimensions[column].outlineLevel == 1, column
    assert ws["A4"].alignment.wrap_text is True
    assert "\n" in ws["A4"].value


def test_export_preserves_all_nineteen_purple_cells(tmp_path, portfolio, ledger):
    out = export_workbook(tmp_path / "export.xlsx", portfolio, ledger)
    ws = openpyxl.load_workbook(out)["Open Trades"]
    purple = [
        cell.coordinate
        for row in ws.iter_rows()
        for cell in row
        if cell.font is not None
        and cell.font.color is not None
        and str(cell.font.color.rgb).upper().endswith("7E3794")
    ]
    assert len(purple) == 19
    assert {"L9", "M9", "Y10", "Z10", "P11", "R12", "Z13"} <= set(purple)


def test_export_sheet6_ledger(tmp_path, portfolio, ledger):
    out = export_workbook(tmp_path / "export.xlsx", portfolio, ledger)
    ws = openpyxl.load_workbook(out)["Sheet6"]
    assert ws["A2"].value.strftime("%Y-%m") == "2022-02"
    assert ws["A3"].value == "NKE" and ws["B3"].value == -354
    assert ws["A4"].value == "GLD" and ws["C4"].value == -680
    assert ws["A8"].value.strftime("%Y-%m") == "2022-03"
    assert ws["A9"].value == "XOP"


# --------------------------------------------------------------------------- #
# Export -> import round trip
# --------------------------------------------------------------------------- #
def test_export_import_round_trip_preserves_every_value(tmp_path, portfolio, ledger):
    out = export_workbook(tmp_path / "roundtrip.xlsx", portfolio, ledger)
    reimported = import_workbook(out)

    original = C.render_values(portfolio)
    again = C.render_values(reimported.portfolio)
    assert set(again) == set(original)
    for cell in original:
        assert math.isclose(float(again[cell]), float(original[cell]), rel_tol=1e-9, abs_tol=1e-9), cell

    assert [(b.row_start, b.row_end) for b in reimported.portfolio.blocks] == [(5, 6), (7, 8), (9, 10), (11, 13)]
    by_row = {leg.sheet_row: leg for leg in reimported.portfolio.all_legs()}
    assert by_row[5].entry_price is None
    assert by_row[5].extra_cells == {"X": 0.0}
    assert by_row[11].has_ev_column is False
    assert set(by_row[10].purple_cells) == {"L", "M", "Y", "Z"}
    assert reimported.portfolio.blocks[3].net_divisor == 1000.0
    assert reimported.portfolio.blocks[3].roll.literal_zero is True
    assert [(e.ticker, e.pnl, e.extra) for e in reimported.ledger.months[0].entries] == [
        ("NKE", -354.0, None),
        ("GLD", -295.0, -680.0),
        ("ADBE", -737.0, None),
        ("FB", -1090.0, None),
        ("SPX", -1000.0, None),
    ]


def test_export_import_round_trip_preserves_roll_values(tmp_path, portfolio, ledger):
    out = export_workbook(tmp_path / "roundtrip.xlsx", portfolio, ledger)
    reimported = import_workbook(out)
    for block, original in zip(reimported.portfolio.blocks, portfolio.blocks):
        assert block.roll.value() == pytest.approx(original.roll.value())
        assert block.roll.literal_zero is original.roll.literal_zero


def test_export_import_round_trip_preserves_roll_pair_history(tmp_path, portfolio, ledger):
    out = export_workbook(tmp_path / "roundtrip.xlsx", portfolio, ledger)
    reimported = import_workbook(out)
    for block, original in zip(reimported.portfolio.blocks, portfolio.blocks):
        assert [(p.new_px, p.closed_px) for p in block.roll.pairs] == [
            (p.new_px, p.closed_px) for p in original.roll.pairs
        ]
        assert block.roll.credits == original.roll.credits


def test_export_import_round_trip_warns_about_checksum_mismatch(tmp_path, portfolio, ledger):
    out = export_workbook(tmp_path / "roundtrip.xlsx", portfolio, ledger)
    result = import_workbook(out)
    assert any("checksum" in warning.lower() for warning in result.warnings)


def test_export_import_round_trip_after_an_edit(tmp_path, portfolio, ledger):
    leg8 = portfolio.blocks[1].legs[1]
    leg8.visible.mark_price = 3.0
    leg8.qty = 50.0
    out = export_workbook(tmp_path / "edited.xlsx", portfolio, ledger)
    reimported = import_workbook(out)
    assert C.render_values(reimported.portfolio)["Y8"] == pytest.approx(
        C.render_values(portfolio)["Y8"]
    )
    assert reimported.portfolio.blocks[1].legs[1].visible.mark_price == pytest.approx(3.0)
    assert reimported.portfolio.blocks[1].legs[1].qty == pytest.approx(50.0)


def test_bootstrap_can_persist_an_exported_workbook(tmp_path, workbook_path, portfolio, ledger):
    out = export_workbook(tmp_path / "exported.xlsx", portfolio, ledger)
    store = Store(tmp_path / "fresh.json")
    result = bootstrap(store=store, xlsx_path=out)
    assert result.origin == "xlsx"
    assert store.exists()
    assert C.render_values(result.portfolio)["P40"] == pytest.approx(36.4)


# --------------------------------------------------------------------------- #
# Import error paths
# --------------------------------------------------------------------------- #
def test_import_missing_file_raises(tmp_path):
    with pytest.raises(WorkbookImportError, match="not found"):
        import_workbook(tmp_path / "absent.xlsx")


def test_import_corrupt_file_raises(tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"this is not a zip archive")
    with pytest.raises(WorkbookImportError, match="Cannot read"):
        import_workbook(bad)


def test_import_non_tracker_workbook_raises(tmp_path):
    path = _minimal_workbook(tmp_path / "other.xlsx", sheet_name="Sheet1")
    with pytest.raises(WorkbookImportError, match="Open Trades"):
        import_workbook(path)


def test_import_warns_on_header_mismatch(tmp_path):
    path = _minimal_workbook(tmp_path / "mismatch.xlsx")
    wb = openpyxl.load_workbook(path)
    wb["Open Trades"]["A4"] = "Trade Action"  # missing the stored second line
    wb.save(path)
    result = import_workbook(path)
    assert any("Header mismatch in column A" in warning for warning in result.warnings)


def test_import_warns_on_unknown_extra_column(tmp_path):
    path = _minimal_workbook(tmp_path / "extra.xlsx", extra_headers={"AA": "Notes"})
    result = import_workbook(path)
    assert any("Unknown extra column AA" in warning for warning in result.warnings)


def test_import_warns_when_sheet6_is_missing(tmp_path):
    path = _minimal_workbook(tmp_path / "nosheet6.xlsx")
    result = import_workbook(path)
    assert any("Sheet6" in warning for warning in result.warnings)
    assert result.ledger.months == []


def test_import_minimal_workbook_values_and_blank_price(tmp_path):
    path = _minimal_workbook(tmp_path / "minimal.xlsx")
    result = import_workbook(path)
    leg = result.portfolio.all_legs()[0]
    assert leg.sheet_row == 5
    assert leg.symbol == ".AMD220715C85"
    assert leg.entry_price == pytest.approx(1.02)
    assert leg.has_ev_column is True
    assert C.leg_entry_value(leg) == pytest.approx(-1.02)
    assert C.leg_mark_value(leg) == pytest.approx(-0.51)
    assert C.leg_pl_open(leg) == pytest.approx(0.51)
    assert C.portfolio_total_delta(result.portfolio.blocks) == pytest.approx(-0.159)


def test_import_blank_entry_price_stays_none(tmp_path):
    path = _minimal_workbook(tmp_path / "blank_price.xlsx")
    wb = openpyxl.load_workbook(path)
    wb["Open Trades"]["F5"] = None
    wb.save(path)
    leg = import_workbook(path).portfolio.all_legs()[0]
    assert leg.entry_price is None
    assert C.leg_entry_value(leg) == 0.0


def test_import_splits_blocks_on_non_empty_strategy(tmp_path):
    path = tmp_path / "blocks.xlsx"
    workbook = Workbook()
    sheet = workbook.worksheets[0]
    sheet.title = "Open Trades"
    _write_headers(sheet)
    for row in range(5, 10):
        sheet[f"C{row}"] = f".AMD220715C8{row}"
        sheet[f"E{row}"] = 1
        sheet[f"F{row}"] = 1
        sheet[f"J{row}"] = 1
    sheet["B5"] = "Diagonal"
    sheet["B8"] = "BWB"
    sheet["I7"] = 100
    sheet["I9"] = 200
    sheet["A40"] = "TOTAL"
    workbook.save(path)

    result = import_workbook(path)
    assert [(b.row_start, b.row_end) for b in result.portfolio.blocks] == [(5, 7), (8, 9)]
    assert result.portfolio.blocks[0].strategy == "Diagonal"
    assert result.portfolio.blocks[1].strategy == "BWB"
    assert result.portfolio.blocks[0].capital == pytest.approx(100.0)
    assert result.portfolio.blocks[1].capital == pytest.approx(200.0)


def test_import_keeps_rows_with_qty_but_blank_symbol(tmp_path):
    path = _minimal_workbook(tmp_path / "blank_symbol.xlsx")
    wb = openpyxl.load_workbook(path)
    wb["Open Trades"]["C5"] = None
    wb.save(path)
    legs = import_workbook(path).portfolio.all_legs()
    assert len(legs) == 1
    assert legs[0].symbol is None
    assert legs[0].qty == -1.0


def test_import_totals_mismatch_is_reported(tmp_path):
    path = _minimal_workbook(tmp_path / "bad_totals.xlsx")
    wb = openpyxl.load_workbook(path)
    wb["Open Trades"]["P40"] = 999
    wb.save(path)
    result = import_workbook(path)
    assert any("P40 cached" in warning for warning in result.warnings)


def test_import_detects_net_divisor_from_formula_without_comment(tmp_path):
    path = tmp_path / "divisor.xlsx"
    workbook = Workbook()
    sheet = workbook.worksheets[0]
    sheet.title = "Open Trades"
    _write_headers(sheet)
    sheet["C5"] = ".QQQ220330P326"
    sheet["E5"] = 500
    sheet["F5"] = 3.28
    sheet["J5"] = 1.17
    sheet["K5"] = "=SUM(L5:L5)/(1000)"
    sheet["A40"] = "TOTAL"
    workbook.save(path)
    result = import_workbook(path)
    assert result.portfolio.blocks[0].net_divisor == 1000.0
    assert any("divisor override 1000" in warning for warning in result.warnings)


def test_import_of_export_without_comments_still_derives_values(tmp_path, portfolio, ledger):
    out = export_workbook(tmp_path / "export.xlsx", portfolio, ledger)
    wb = openpyxl.load_workbook(out)
    ws = wb["Open Trades"]
    for coordinate in ("C5", "C6", "C7", "C8", "C9", "C10", "C11", "C12", "C13"):
        ws[coordinate].comment = None
    wb.save(out)
    result = import_workbook(out)
    # Visible columns are authoritative; missing comments only mean "no quote".
    assert C.render_values(result.portfolio)["P40"] == pytest.approx(36.4)
    assert all(leg.quote.is_placeholder for leg in result.portfolio.all_legs())
