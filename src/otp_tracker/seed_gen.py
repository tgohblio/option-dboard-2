"""Dev tool: convert ``data/otptracker_fixtures.json`` into the bundled seed.

Usage::

    python -m otp_tracker.seed_gen ../data/seed_fixture.json > src/otp_tracker/seed_otp.json

The fixture is the machine-readable export of the real workbook and is
authoritative (docs/spec.md §0, §10.4).  This module is a dev-only tool: the app
never calls it at runtime.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .models import (
    Leg,
    MonthlyLedger,
    MonthlyMonth,
    MonthlyPnlEntry,
    Portfolio,
    PositionBlock,
    QuoteSnapshot,
    RollLedger,
    VisibleQuote,
    coerce_float,
)
from .store import make_payload

PURPLE = "FF7E3794"
COLUMN_ORDER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_K_DIVISOR_RE = re.compile(r"/\(\s*(\d+(?:\.\d+)?)\s*\)")


def _cell(row: dict[str, Any], column: str) -> dict[str, Any] | None:
    return row.get(column)


def _value(row: dict[str, Any], column: str) -> Any:
    cell = _cell(row, column)
    return None if cell is None else cell.get("value")


def _number(row: dict[str, Any], column: str, default: float = 0.0) -> float:
    return coerce_float(_value(row, column), default)


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _quote(comments: dict[str, Any], row_number: int) -> QuoteSnapshot:
    comment = comments.get(f"C{row_number}")
    text = ""
    if isinstance(comment, dict):
        text = str(comment.get("text") or "")
    elif isinstance(comment, str):
        text = comment
    if not text.strip():
        return QuoteSnapshot()
    try:
        return QuoteSnapshot.from_json_text(text)
    except ValueError:
        return QuoteSnapshot(raw=text.strip())


def _leg(row_number: int, row: dict[str, Any], comments: dict[str, Any]) -> Leg:
    symbol = _value(row, "C")
    symbol_text = str(symbol).strip() if symbol not in (None, "") else None
    purple = sorted(
        (col for col in COLUMN_ORDER if (row.get(col) or {}).get("font_rgb") == PURPLE),
        key=COLUMN_ORDER.index,
    )
    entry_price_cell = _cell(row, "F")
    entry_price = None
    if entry_price_cell is not None and entry_price_cell.get("value") not in (None, ""):
        entry_price = coerce_float(entry_price_cell.get("value"))
    return Leg(
        sheet_row=row_number,
        symbol=symbol_text or None,
        entry_date=_date(_value(row, "D")),
        qty=_number(row, "E"),
        entry_price=entry_price,
        action_note=(str(_value(row, "A")).strip() or None) if _value(row, "A") else None,
        quote=_quote(comments, row_number),
        visible=VisibleQuote(
            mark_price=_number(row, "J"),
            underlying_price=_number(row, "N"),
            price_change=_number(row, "O"),
            delta=_number(row, "Q"),
            theta=_number(row, "S"),
            itm=bool(_value(row, "T")),
            ev=_number(row, "V"),
            dte=_number(row, "W"),
        ),
        has_ev_column=_cell(row, "U") is not None,
        purple_cells=purple,
    )


def _block_roll(row: dict[str, Any]) -> RollLedger:
    cell = _cell(row, "X")
    if cell is None:
        return RollLedger()
    formula = cell.get("formula")
    if formula:
        return RollLedger.from_expression(str(formula))
    value = coerce_float(cell.get("value"))
    if value == 0.0:
        return RollLedger(literal_zero=True)
    return RollLedger(credits=[value / 100.0])


def _blocks(rows: dict[int, dict[str, Any]], comments: dict[str, Any]) -> list[PositionBlock]:
    # Only real leg rows: the totals row (40) carries A/P/R but no symbol or qty.
    numbers = sorted(
        number for number, row in rows.items() if _value(row, "C") not in (None, "") or _value(row, "E") is not None
    )
    groups: list[list[int]] = []
    current: list[int] = []
    for number in numbers:
        strategy_cell = _value(rows[number], "B")
        if not current:
            current = [number]
        elif strategy_cell not in (None, ""):
            groups.append(current)
            current = [number]
        else:
            current.append(number)
    if current:
        groups.append(current)

    blocks: list[PositionBlock] = []
    for group in groups:
        last_row = group[-1]
        last_cells = rows[last_row]
        last_qty = _number(last_cells, "E")
        net_divisor: float | None = None
        k_cell = _cell(last_cells, "K")
        if k_cell and k_cell.get("formula"):
            match = _K_DIVISOR_RE.search(str(k_cell["formula"]))
            if match:
                divisor = float(match.group(1))
                if divisor != last_qty:
                    net_divisor = divisor
        capital = _value(last_cells, "I")
        block = PositionBlock(
            row_start=group[0],
            row_end=last_row,
            strategy=(str(_value(rows[group[0]], "B")).strip() or None)
            if _value(rows[group[0]], "B")
            else None,
            capital=None if capital is None else coerce_float(capital),
            net_divisor=net_divisor,
            roll=_block_roll(last_cells),
        )
        block.legs = [_leg(number, rows[number], comments) for number in group]
        for leg in block.legs:
            if leg.sheet_row == last_row:
                continue
            stray = _cell(rows[leg.sheet_row], "X")
            if stray is not None:
                leg.extra_cells["X"] = coerce_float(stray.get("value"))
        blocks.append(block)
    return blocks


def _ledger(sheet6: dict[str, Any]) -> MonthlyLedger:
    ledger = MonthlyLedger()
    current: MonthlyMonth | None = None
    for _, row in sorted(sheet6.get("rows", {}).items(), key=lambda item: int(item[0])):
        a_cell = row.get("A") or {}
        b_cell = row.get("B") or {}
        if a_cell.get("numfmt") == "mmm yyyy" or (
            isinstance(a_cell.get("value"), str) and "00:00:00" in str(a_cell.get("value")) and not b_cell
        ):
            month = _date(a_cell.get("value"))
            if month is None:
                continue
            current = MonthlyMonth(month=date(month.year, month.month, 1))
            ledger.months.append(current)
            continue
        if current is None or a_cell.get("value") in (None, ""):
            continue
        extra = row.get("C") or {}
        current.entries.append(
            MonthlyPnlEntry(
                ticker=str(a_cell.get("value")).strip(),
                pnl=coerce_float(b_cell.get("value")),
                extra=None if extra.get("value") is None else coerce_float(extra.get("value")),
            )
        )
    ledger.sort()
    return ledger


def portfolio_from_fixture(fixture: dict[str, Any]) -> tuple[Portfolio, MonthlyLedger]:
    open_trades = fixture["open_trades"]
    rows = {int(key): value for key, value in open_trades["data_rows"].items()}
    portfolio = Portfolio(
        as_of_date=_date(_value(open_trades["layout_rows_1_4"]["3"], "Y")),
        blocks=_blocks(rows, open_trades.get("comments", {})),
    )
    return portfolio, _ledger(fixture.get("sheet6", {}))


def payload_from_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    portfolio, ledger = portfolio_from_fixture(fixture)
    source = fixture.get("source") or {}
    return make_payload(
        portfolio,
        ledger,
        source={
            "file": source.get("path", ""),
            "sha256": source.get("sha256"),
            "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path, help="path to otptracker_fixtures.json")
    parser.add_argument("-o", "--output", type=Path, default=None, help="write here instead of stdout")
    args = parser.parse_args(argv)

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    payload = payload_from_fixture(fixture)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
