"""Golden acceptance check against docs/spec.md §14.2 (run: .venv/bin/python verify_acceptance.py)."""
from __future__ import annotations

import json
import math
import sys
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

failures: list[str] = []


def check(label: str, actual, expected) -> None:
    if isinstance(expected, (str, bool)) or expected is None or isinstance(expected, (list, tuple, set, dict)):
        ok = actual == expected
    else:
        ok = math.isclose(float(actual), float(expected), rel_tol=1e-8, abs_tol=1e-9)
    if not ok:
        failures.append(f"{label}: actual={actual!r} expected={expected!r}")


boot = store.load_seed()
portfolio, ledger = boot.portfolio, boot.ledger
print("bootstrap warnings:", boot.warnings)
values = C.render_values(portfolio)

for cell, expected in GOLDEN.items():
    if cell not in values:
        if cell.startswith("U"):
            continue
        failures.append(f"{cell}: missing from render_values")
        continue
    check(cell, values[cell], expected)

# absent U cells for rows 11-13
for row in (11, 12, 13):
    if f"U{row}" in values:
        failures.append(f"U{row}: should be absent (no EV column)")

# structure
check("blocks", len(portfolio.blocks), 4)
check("legs", len(portfolio.all_legs()), 9)
check("block_rows", [(b.row_start, b.row_end) for b in portfolio.blocks], [(5, 6), (7, 8), (9, 10), (11, 13)])
check("as_of", portfolio.as_of_date.isoformat(), "2021-12-18")
check("net_divisor_b4", portfolio.blocks[3].net_divisor, 1000.0)
check("net_divisor_overridden", C.net_divisor_overridden(portfolio.blocks[3]), True)
check("b1_entry_prices_none", [leg.entry_price for b in portfolio.blocks[:1] for leg in b.legs], [None, None])
check("extra_cells_x5", portfolio.blocks[0].legs[0].extra_cells.get("X"), 0.0)
check("has_ev_rows", [leg.sheet_row for leg in portfolio.all_legs() if leg.has_ev_column], [5, 6, 7, 8, 9, 10])

# quotes
expected_info = {
    5: "DIS Mar 18 2022 145 Call", 6: "DIS Apr 14 2022 135 Call (Weekly)",
    7: "AMD Jul 15 2022 85 Call", 8: "AMD Oct 21 2022 100 Call",
    10: "JPM Sep 16 2022 130 Call",
}
by_row = {leg.sheet_row: leg for leg in portfolio.all_legs()}
for row, info in expected_info.items():
    check(f"info{row}", by_row[row].quote.info, info)
    if by_row[row].quote.is_placeholder:
        failures.append(f"row {row}: real quote flagged placeholder")
for row in (9, 11, 12, 13):
    if not by_row[row].quote.is_placeholder:
        failures.append(f"row {row}: placeholder quote not flagged")
    check(f"ticker{row}", by_row[row].quote.ticker, "None")
    check(f"visible_mark{row}", by_row[row].visible.mark_price, 1.17 if row > 9 else 2.46)

# purple set
purple = {leg.sheet_row: set(leg.purple_cells) for leg in portfolio.all_legs()}
expected_purple = {9: {"L", "M"}, 10: {"L", "M", "Y", "Z"}, 11: {"L", "M", "P", "R"},
                  12: {"L", "M", "P", "R"}, 13: {"L", "M", "P", "R", "Z"}}
for row, cols in expected_purple.items():
    check(f"purple{row}", purple[row], cols)

# ledger
months = [(m.month.isoformat(), [(e.ticker, e.pnl, e.extra) for e in m.entries]) for m in ledger.months]
check("months", [m[0] for m in months], ["2022-02-01", "2022-03-01"])
check("feb", months[0][1], [("NKE", -354.0, None), ("GLD", -295.0, -680.0), ("ADBE", -737.0, None),
                            ("FB", -1090.0, None), ("SPX", -1000.0, None)])
check("mar", months[1][1], [("XOP", -265.0, None)])
check("feb_total", round(ledger.months[0].total, 6), -3476.0)

# formatting spot checks (display parity)
text, color = formatting.format_cell(755, "money")
check("fmt_755_text", text, "755.00")
text, color = formatting.format_cell(-108, "money")
check("fmt_-108_text", text, "(108.00)")
text, color = formatting.format_cell(-1.00980873, "percent")
check("fmt_pct", text, "-100.98%")
text, _ = formatting.format_cell(-650, "money")
check("fmt_-650_text", text, "(650.00)")
text, _ = formatting.format_cell(0, "money")
check("fmt_zero", text, "0.0")
text, _ = formatting.format_cell(-100, "qty")
check("fmt_qty", text, "(100)")
text, _ = formatting.format_cell(None, "money")
check("fmt_none", text, "—")

print(f"checked {len(GOLDEN)} golden cells + structure/quote/purple/ledger/format assertions")
if failures:
    print(f"FAIL ({len(failures)}):")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("ALL ACCEPTANCE CHECKS PASS")
print("totals P40=%.10g R40=%.10g; blocks=%d legs=%d" % (
    values["P40"], values["R40"], len(portfolio.blocks), len(portfolio.all_legs())))
