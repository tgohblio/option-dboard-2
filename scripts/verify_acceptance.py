"""Golden acceptance check against docs/spec.md §14.2.

Run from the project root:

    .venv/bin/python scripts/verify_acceptance.py

Three passes, all required:
  1. bundled seed           — ``src/otp_tracker/seed_otp.json``
  2. live workbook          — ``~/Downloads/OTPTracker.xlsx`` (skipped if absent)
  3. export -> re-import    — layout-preserving .xlsx round trip (temp dir)
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from otp_tracker import calculations as C, formatting, store  # noqa: E402

GOLDEN = {
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
    5: "DIS Mar 18 2022 145 Call", 6: "DIS Apr 14 2022 135 Call (Weekly)",
    7: "AMD Jul 15 2022 85 Call", 8: "AMD Oct 21 2022 100 Call",
    10: "JPM Sep 16 2022 130 Call",
}
EXPECTED_PURPLE = {
    9: {"L", "M"}, 10: {"L", "M", "Y", "Z"}, 11: {"L", "M", "P", "R"},
    12: {"L", "M", "P", "R"}, 13: {"L", "M", "P", "R", "Z"},
}

failures: list[str] = []
XLSX = Path.home() / "Downloads" / "OTPTracker.xlsx"


def check(label: str, actual, expected) -> None:
    if isinstance(expected, (str, bool)) or expected is None or isinstance(expected, (list, tuple, set, dict)):
        ok = actual == expected
    else:
        try:
            ok = math.isclose(float(actual), float(expected), rel_tol=1e-8, abs_tol=1e-9)
        except (TypeError, ValueError):
            ok = False
    if not ok:
        failures.append(f"{label}: actual={actual!r} expected={expected!r}")


def check_portfolio(portfolio, ledger, prefix: str = "") -> None:
    """Every assertion from §14.2 against one loaded Portfolio/MonthlyLedger."""
    values = C.render_values(portfolio)

    for cell, expected in GOLDEN.items():
        if cell not in values:
            if cell.startswith("U"):
                continue
            failures.append(f"{prefix}{cell}: missing from render_values")
            continue
        check(f"{prefix}{cell}", values[cell], expected)

    for row in (11, 12, 13):
        if f"U{row}" in values:
            failures.append(f"{prefix}U{row}: should be absent (no EV column)")

    # structure
    check(f"{prefix}blocks", len(portfolio.blocks), 4)
    check(f"{prefix}legs", len(portfolio.all_legs()), 9)
    check(f"{prefix}block_rows", [(b.row_start, b.row_end) for b in portfolio.blocks],
          [(5, 6), (7, 8), (9, 10), (11, 13)])
    check(f"{prefix}as_of", portfolio.as_of_date.isoformat(), "2021-12-18")
    check(f"{prefix}net_divisor_b4", portfolio.blocks[3].net_divisor, 1000.0)
    check(f"{prefix}net_divisor_overridden", C.net_divisor_overridden(portfolio.blocks[3]), True)
    check(f"{prefix}b1_entry_prices_none",
          [leg.entry_price for b in portfolio.blocks[:1] for leg in b.legs], [None, None])
    check(f"{prefix}extra_cells_x5", portfolio.blocks[0].legs[0].extra_cells.get("X"), 0.0)
    check(f"{prefix}has_ev_rows",
          [leg.sheet_row for leg in portfolio.all_legs() if leg.has_ev_column], [5, 6, 7, 8, 9, 10])

    # quotes
    by_row = {leg.sheet_row: leg for leg in portfolio.all_legs()}
    for row, info in EXPECTED_INFO.items():
        check(f"{prefix}info{row}", by_row[row].quote.info, info)
        if by_row[row].quote.is_placeholder:
            failures.append(f"{prefix}row {row}: real quote flagged placeholder")
    for row in (9, 11, 12, 13):
        if not by_row[row].quote.is_placeholder:
            failures.append(f"{prefix}row {row}: placeholder quote not flagged")
        check(f"{prefix}ticker{row}", by_row[row].quote.ticker, "None")
        check(f"{prefix}visible_mark{row}", by_row[row].visible.mark_price, 1.17 if row > 9 else 2.46)

    # purple set (ignoring rows that legitimately have no purple cells)
    purple = {leg.sheet_row: set(leg.purple_cells) for leg in portfolio.all_legs() if leg.purple_cells}
    for row, cols in EXPECTED_PURPLE.items():
        check(f"{prefix}purple{row}", purple.get(row, set()), cols)

    # ledger
    months = [(m.month.isoformat(), [(e.ticker, e.pnl, e.extra) for e in m.entries])
              for m in ledger.months]
    check(f"{prefix}months", [m[0] for m in months], ["2022-02-01", "2022-03-01"])
    check(f"{prefix}feb", months[0][1], [("NKE", -354.0, None), ("GLD", -295.0, -680.0),
                                         ("ADBE", -737.0, None), ("FB", -1090.0, None),
                                         ("SPX", -1000.0, None)])
    check(f"{prefix}mar", months[1][1], [("XOP", -265.0, None)])
    if ledger.months:
        check(f"{prefix}feb_total", round(ledger.months[0].total, 6), -3476.0)


def check_formatting() -> None:
    """Display parity spot checks (§14.1)."""
    check("fmt_755_text", formatting.format_cell(755, "money")[0], "755.00")
    check("fmt_-108_text", formatting.format_cell(-108, "money")[0], "(108.00)")
    check("fmt_pct", formatting.format_cell(-1.00980873, "percent")[0], "-100.98%")
    check("fmt_-650_text", formatting.format_cell(-650, "money")[0], "(650.00)")
    check("fmt_zero", formatting.format_cell(0, "money")[0], "0.0")
    check("fmt_qty", formatting.format_cell(-100, "qty")[0], "(100)")
    check("fmt_none", formatting.format_cell(None, "money")[0], "—")


# ---------------------------------------------------------------- pass 1: seed
boot = store.load_seed()
print(f"[seed]        warnings: {boot.warnings or 'none'}")
check_portfolio(boot.portfolio, boot.ledger, prefix="seed:")
check_formatting()

# -------------------------------------------------------- pass 2: live workbook
if XLSX.is_file():
    from otp_tracker import excel_io

    imported = excel_io.import_workbook(str(XLSX))
    print(f"[workbook]    {XLSX.name} warnings: {imported.warnings or 'none'}")
    check("workbook_checksum", imported.portfolio.source_checksum,
          "525d60cbfd87cfef2078c8bf068eddecdf632969fd8c148172f5c7519ba525cd")
    check_portfolio(imported.portfolio, imported.ledger, prefix="live:")

    # ------------------------------------------------- pass 3: export round trip
    with tempfile.TemporaryDirectory(prefix="otp_accept_") as tmp:
        out = Path(tmp) / "OTPTracker_roundtrip.xlsx"
        excel_io.export_workbook(str(out), imported.portfolio, imported.ledger)
        reimported = excel_io.import_workbook(str(out))
        print(f"[export]      {out.name} warnings: {reimported.warnings or 'none'}")
        check_portfolio(reimported.portfolio, reimported.ledger, prefix="roundtrip:")

        import openpyxl

        ws = openpyxl.load_workbook(out)["Open Trades"]
        check("rt_title", ws["I1"].value, "OTP PORFOLIO TRACKER - OPEN TRADES")
        check("rt_freeze", ws.freeze_panes, "A5")
        check("rt_total_label", str(ws["A40"].value).strip().upper(), "TOTAL")
        check("rt_p40", ws["P40"].value, 36.4)
        check("rt_r40", ws["R40"].value, 9.2)
        check("rt_u11_blank", ws["U11"].value, None)
        check("rt_comment_c5", "ticker" in (ws["C5"].comment.text if ws["C5"].comment else ""), True)
        purple = [c for row in ws.iter_rows() for c in row
                  if c.font is not None and c.font.color is not None
                  and str(c.font.color.rgb).upper().endswith("7E3794")]
        check("rt_purple_cells", len(purple), 19)
else:
    print(f"[workbook]    skipped — {XLSX} not found")
    print("[export]      skipped — needs the workbook")

seed_values = C.render_values(boot.portfolio)
print(f"checked {len(GOLDEN)} golden cells per pass + structure/quote/purple/ledger/format assertions")
print(f"totals P40={seed_values['P40']:.10g} R40={seed_values['R40']:.10g}; "
      f"blocks={len(boot.portfolio.blocks)} legs={len(boot.portfolio.all_legs())}")
if failures:
    print(f"FAIL ({len(failures)}):")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("ALL ACCEPTANCE CHECKS PASS")
