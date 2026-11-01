"""openpyxl import/export for OTPTracker.xlsx (docs/spec.md §10.3).

Import is the only way real workbook data enters the app; export writes a
faithful copy of the two sheets with computed cells stored as **values** (the app
recomputes formulas in Python, so no Excel recalculation is needed).

The workbook never becomes the live database — :mod:`otp_tracker.store` is.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from . import calculations
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
from .store import PROJECT_ROOT, bundled_seed_path

__all__ = [
    "WorkbookImportError",
    "ImportResult",
    "HEADERS",
    "COLUMN_WIDTHS",
    "NUMFMT_BY_KEY",
    "import_workbook",
    "export_workbook",
    "sha256_file",
]

OPEN_TRADES = "Open Trades"
SHEET6 = "Sheet6"
PURPLE_RGB = "FF7E3794"
TOTALS_ROW = 40
TITLE = "OTP PORFOLIO TRACKER - OPEN TRADES"  # typo preserved from source workbook
TITLE_CELL = "I1"
BANNER_ENTRY_CELL = "C3"
BANNER_MARKET_CELL = "K3"
AS_OF_CELL = "Y3"
HEADER_ROW = 4
FIRST_DATA_ROW = 5
MARKET_COLUMNS = "NOPQRSTUVW"  # the outline group (market data)

_TOTAL_Y_COMMENT = "Sum of P/L open + rolling P/L for the trade"
_TOTAL_Z_COMMENT = "Total P/L divided by capital"

#: Stored multi-line header labels, exactly as in row 4 of the workbook.
HEADERS: dict[str, str] = {
    "A": "Trade Action\nNotif. #",
    "B": "Strategy",
    "C": "OPRA",
    "D": "Entry Date",
    "E": "Qty",
    "F": "Entry Price",
    "G": "Net\nEntry\nPrice",
    "H": "Entry Value",
    "I": "Capital/\nBuying Power\n(Max Loss)",
    "J": "MARK Price",
    "K": "Net\nMARK Price",
    "L": "MARK\nValue",
    "M": "P/L Open",
    "N": "Underlying\nPrice",
    "O": "Price Change",
    "P": "Total Delta",
    "Q": "Delta\nper share",
    "R": "Total Theta",
    "S": "Theta\nper share",
    "T": "ITM",
    "U": "Total\nExtrinsic\nValue(EV)",
    "V": "EV\nper share",
    "W": "Remaining\nDTE",
    "X": "Rolling\nP/L",
    "Y": "Total\nP/L",
    "Z": "% ROI",
}

#: Source column widths (docs/spec.md §3.1).
COLUMN_WIDTHS: dict[str, float] = {
    "A": 16.5, "B": 7.25, "C": 20.25, "D": 11.0, "E": 8.75, "F": 9.13, "G": 11.63,
    "H": 9.63, "I": 35.25, "J": 9.63, "K": 12.63, "L": 9.63, "M": 7.63, "N": 12.75,
    "O": 11.0, "P": 8.75, "Q": 12.13, "R": 9.0, "S": 12.25, "T": 5.63, "U": 19.0,
    "V": 10.13, "W": 12.0, "X": 8.0, "Y": 10.75, "Z": 7.0,
}

_MONEY = "[Color10]#,###.00;([Red]#,###.00);[Black]0.0"
_NUMFMT_MONEY_SIMPLE = "#,##0.00"
_NUMFMT_QTY = "[Color 10]#,##0;([Red]#,##0);[Black]0.0;[Magenta]_(@_)"

#: Per-column Excel number format for export (mirrors the source formats).
NUMFMT_BY_KEY: dict[str, str] = {
    "A": "General", "B": "General", "C": "General", "D": "M/d/yyyy", "E": _NUMFMT_QTY,
    "F": _NUMFMT_MONEY_SIMPLE, "G": _NUMFMT_MONEY_SIMPLE, "H": _MONEY,
    "I": _NUMFMT_MONEY_SIMPLE, "J": _MONEY, "K": _MONEY, "L": _MONEY, "M": _MONEY,
    "N": "General", "O": _MONEY, "P": _MONEY, "Q": _MONEY, "R": _MONEY, "S": _MONEY,
    "T": "General", "U": '"$"#,##0.00', "V": _MONEY, "W": "General",
    "X": _NUMFMT_MONEY_SIMPLE, "Y": _MONEY, "Z": "0.00%",
}

_K_LITERAL_DIVISOR_RE = re.compile(r"/\(\s*(\d+(?:\.\d+)?)\s*\)")


class WorkbookImportError(Exception):
    """The workbook is missing, unreadable, or not an OTP tracker."""


@dataclass
class ImportResult:
    portfolio: Portfolio
    ledger: MonthlyLedger
    source: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _known_sha256() -> str | None:
    """sha256 of the reference workbook, when the fixture is available."""
    for candidate in (PROJECT_ROOT / "data" / "seed_fixture.json", bundled_seed_path()):
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        sha = (payload.get("source") or {}).get("sha256")
        if sha:
            return str(sha)
    return None


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalise_header(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\n", " ")).strip().lower()


def _purple_columns(worksheet, row: int, warnings: list[str]) -> list[str]:
    purple: list[str] = []
    for column in HEADERS:
        cell = worksheet[f"{column}{row}"]
        colour = cell.font.color if cell.font else None
        rgb = getattr(colour, "rgb", None)
        if isinstance(rgb, str) and rgb.upper().endswith("7E3794"):
            purple.append(column)
    return purple


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #
def import_workbook(path: str | Path) -> ImportResult:
    """Read an OTPTracker workbook into models.

    Raises :class:`WorkbookImportError` for a missing, unreadable, or structurally
    wrong file so the caller can show one clear message.
    """
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise WorkbookImportError(f"Workbook not found: {file_path}")

    warnings: list[str] = []
    checksum = sha256_file(file_path)
    known = _known_sha256()
    if known and checksum != known:
        warnings.append(
            f"Workbook checksum {checksum[:12]}… differs from the reference ({known[:12]}…) — imported as-is."
        )

    try:
        values_wb = load_workbook(file_path, data_only=True)
        formulas_wb = load_workbook(file_path, data_only=False)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many types for bad zip/xml
        raise WorkbookImportError(f"Cannot read {file_path.name}: {exc}") from exc

    if OPEN_TRADES not in values_wb.sheetnames:
        raise WorkbookImportError(f"{file_path.name} has no '{OPEN_TRADES}' sheet")

    values_ws = values_wb[OPEN_TRADES]
    formulas_ws = formulas_wb[OPEN_TRADES]

    for column, expected in HEADERS.items():
        found = values_ws[f"{column}{HEADER_ROW}"].value
        if _normalise_header(found) != _normalise_header(expected):
            warnings.append(
                f"Header mismatch in column {column}: expected {expected!r}, found {found!r}"
            )
    for column in range(len(HEADERS) + 1, values_ws.max_column + 1):
        header = values_ws.cell(HEADER_ROW, column).value
        if header not in (None, ""):
            warnings.append(f"Unknown extra column {get_column_letter(column)} ({header!r}) ignored")

    rows: dict[int, dict[str, Any]] = {}
    totals_row: int | None = None
    for row in range(FIRST_DATA_ROW, values_ws.max_row + 1):
        label = values_ws.cell(row, 1).value
        if isinstance(label, str) and label.strip().upper() == "TOTAL":
            totals_row = row
            break
        symbol = _text(values_ws.cell(row, 3).value)
        qty = values_ws.cell(row, 5).value
        if symbol is None and qty is None:
            continue
        rows[row] = {"symbol": symbol, "qty": qty}

    portfolio = Portfolio(as_of_date=_as_date(values_ws[AS_OF_CELL].value), source_checksum=checksum)
    portfolio.blocks = _build_blocks(rows, values_ws, formulas_ws, warnings)

    totals = calculations.render_values(portfolio)
    if totals_row is not None:
        for column, key, computed in (
            ("P", "P", totals["P40"]),
            ("R", "R", totals["R40"]),
        ):
            cached = values_ws[f"{column}{totals_row}"].value
            if isinstance(cached, (int, float)) and computed is not None:
                if round(float(cached), 6) != round(float(computed), 6):
                    warnings.append(
                        f"{column}{totals_row} cached {cached} differs from computed {computed:.6f}"
                    )

    ledger = _import_ledger(values_wb, warnings)
    source = {
        "file": str(file_path),
        "sha256": checksum,
        "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return ImportResult(portfolio=portfolio, ledger=ledger, source=source, warnings=warnings)


def _build_blocks(
    rows: dict[int, dict[str, Any]],
    values_ws,
    formulas_ws,
    warnings: list[str],
) -> list[PositionBlock]:
    numbers = sorted(rows)
    groups: list[list[int]] = []
    current: list[int] = []
    for number in numbers:
        if not current:
            current = [number]
            continue
        strategy = _text(values_ws.cell(number, 2).value)
        if strategy:
            groups.append(current)
            current = [number]
        else:
            current.append(number)
    if current:
        groups.append(current)

    blocks: list[PositionBlock] = []
    for group in groups:
        first_row, last_row = group[0], group[-1]
        last_qty = coerce_float(values_ws.cell(last_row, 5).value)
        k_formula = formulas_ws.cell(last_row, 11).value
        net_divisor: float | None = None
        divisor_note = _divisor_from_comment(formulas_ws.cell(last_row, 11).comment)
        if isinstance(k_formula, str):
            match = _K_LITERAL_DIVISOR_RE.search(k_formula)
            if match:
                divisor_note = float(match.group(1))
        if divisor_note is not None and divisor_note != last_qty:
            net_divisor = divisor_note
            warnings.append(
                f"Row {last_row}: Net MARK divisor override {divisor_note:g} "
                f"(last leg qty {last_qty:g}) preserved from the source workbook"
            )
        capital_value = values_ws.cell(last_row, 9).value
        block = PositionBlock(
            row_start=first_row,
            row_end=last_row,
            strategy=_text(values_ws.cell(first_row, 2).value),
            capital=None if capital_value is None else coerce_float(capital_value),
            net_divisor=net_divisor,
            roll=_import_roll(formulas_ws.cell(last_row, 24).value, values_ws.cell(last_row, 24).value),
        )
        block.legs = [_import_leg(row, values_ws, formulas_ws) for row in group]
        for leg in block.legs:
            if leg.sheet_row == last_row:
                continue
            stray = values_ws.cell(leg.sheet_row, 24).value
            if stray is not None:
                leg.extra_cells["X"] = coerce_float(stray)
        blocks.append(block)
    return blocks


def _divisor_from_comment(comment: Any) -> float | None:
    """Recover a K divisor override from the export comment (no formula round-trip)."""
    text = getattr(comment, "text", None)
    if not isinstance(text, str):
        return None
    match = re.search(r"otp-divisor=\s*(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def _import_roll(formula: Any, value: Any) -> RollLedger:
    if isinstance(formula, str) and formula.strip():
        try:
            return RollLedger.from_expression(formula)
        except ValueError:
            pass
    if value in (None, ""):
        return RollLedger()
    number = coerce_float(value)
    if number == 0.0:
        return RollLedger(literal_zero=True)
    return RollLedger(credits=[number / 100.0])


def _import_leg(row: int, values_ws, formulas_ws) -> Leg:
    entry_cell = values_ws.cell(row, 6)
    entry_price = None
    if entry_cell.value not in (None, ""):
        entry_price = coerce_float(entry_cell.value)

    quote = QuoteSnapshot()
    comment = formulas_ws.cell(row, 3).comment
    if comment is not None and comment.text:
        try:
            quote = QuoteSnapshot.from_json_text(comment.text)
        except ValueError:
            quote = QuoteSnapshot(raw=comment.text.strip())

    visible = VisibleQuote(
        mark_price=coerce_float(values_ws.cell(row, 10).value),
        underlying_price=coerce_float(values_ws.cell(row, 14).value),
        price_change=coerce_float(values_ws.cell(row, 15).value),
        delta=coerce_float(values_ws.cell(row, 17).value),
        theta=coerce_float(values_ws.cell(row, 19).value),
        itm=bool(values_ws.cell(row, 20).value),
        ev=coerce_float(values_ws.cell(row, 22).value),
        dte=coerce_float(values_ws.cell(row, 23).value),
    )
    has_ev = values_ws.cell(row, 21).value is not None or formulas_ws.cell(row, 21).value not in (None, "")
    return Leg(
        sheet_row=row,
        symbol=_text(values_ws.cell(row, 3).value),
        entry_date=_as_date(values_ws.cell(row, 4).value),
        qty=coerce_float(values_ws.cell(row, 5).value),
        entry_price=entry_price,
        action_note=_text(values_ws.cell(row, 1).value),
        quote=quote,
        visible=visible,
        has_ev_column=has_ev,
        purple_cells=_purple_columns(formulas_ws, row, []),
    )


def _import_ledger(values_wb, warnings: list[str]) -> MonthlyLedger:
    ledger = MonthlyLedger()
    if SHEET6 not in values_wb.sheetnames:
        warnings.append(f"No '{SHEET6}' sheet — monthly ledger starts empty")
        return ledger
    sheet = values_wb[SHEET6]
    current: MonthlyMonth | None = None
    for row in range(1, sheet.max_row + 1):
        cell_a = sheet.cell(row, 1)
        if cell_a.value in (None, ""):
            continue
        is_month = cell_a.number_format == "mmm yyyy" or isinstance(cell_a.value, (datetime, date))
        if is_month:
            month = _as_date(cell_a.value)
            if month is None:
                continue
            current = MonthlyMonth(month=date(month.year, month.month, 1))
            ledger.months.append(current)
            continue
        if current is None:
            warnings.append(f"{SHEET6}!A{row} has a ticker before any month header — ignored")
            continue
        extra_cell = sheet.cell(row, 3)
        current.entries.append(
            MonthlyPnlEntry(
                ticker=str(cell_a.value).strip(),
                pnl=coerce_float(sheet.cell(row, 2).value),
                extra=None if extra_cell.value in (None, "") else coerce_float(extra_cell.value),
            )
        )
    ledger.sort()
    return ledger


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def _write(ws, coordinate: str, value: Any, fmt: str | None = None, bold: bool = False) -> None:
    cell = ws[coordinate]
    if value is not None:
        cell.value = value
    if fmt:
        cell.number_format = fmt
    if bold:
        cell.font = Font(bold=True)


def export_workbook(path: str | Path, portfolio: Portfolio, ledger: MonthlyLedger) -> Path:
    """Write a faithful ``.xlsx`` (layout, row numbers, formats, comments, widths)."""
    file_path = Path(path).expanduser()
    file_path.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    sheet = workbook.worksheets[0]
    sheet.title = OPEN_TRADES

    _write(sheet, TITLE_CELL, TITLE)
    _write(sheet, BANNER_ENTRY_CELL, "Entry Data", bold=True)
    _write(sheet, BANNER_MARKET_CELL, "Market Data", bold=True)
    if portfolio.as_of_date:
        _write(sheet, AS_OF_CELL, portfolio.as_of_date, NUMFMT_BY_KEY["D"], bold=True)

    for column, label in HEADERS.items():
        cell = sheet[f"{column}{HEADER_ROW}"]
        cell.value = label
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    for column, width in COLUMN_WIDTHS.items():
        sheet.column_dimensions[column].width = width
        if column in MARKET_COLUMNS:
            sheet.column_dimensions[column].outlineLevel = 1

    sheet.freeze_panes = f"A{FIRST_DATA_ROW}"
    sheet["Y4"].comment = Comment(_TOTAL_Y_COMMENT, "otp-tracker")
    sheet["Z4"].comment = Comment(_TOTAL_Z_COMMENT, "otp-tracker")

    for block in portfolio.blocks:
        for leg in block.legs:
            _export_leg(sheet, block, leg)

    for column in ("Y", "Z"):
        cell = sheet[f"{column}{HEADER_ROW}"]
        cell.comment = Comment(
            _TOTAL_Y_COMMENT if column == "Y" else _TOTAL_Z_COMMENT, "otp-tracker"
        )

    totals = calculations.render_values(portfolio)
    _write(sheet, f"A{TOTALS_ROW}", "TOTAL", bold=True)
    _write(sheet, f"P{TOTALS_ROW}", totals["P40"], NUMFMT_BY_KEY["P"], bold=True)
    _write(sheet, f"R{TOTALS_ROW}", totals["R40"], NUMFMT_BY_KEY["R"], bold=True)

    _export_ledger(workbook, ledger)
    workbook.save(file_path)
    return file_path


def _export_leg(sheet, block: PositionBlock, leg: Leg) -> None:
    row = leg.sheet_row
    last = leg.sheet_row == block.row_end
    cells: list[tuple[str, Any]] = [
        ("A", leg.action_note),
        ("B", block.strategy if leg.sheet_row == block.row_start else None),
        ("C", leg.symbol),
        ("D", leg.entry_date),
        ("E", leg.qty),
        ("F", leg.entry_price),
        ("H", calculations.leg_entry_value(leg)),
        ("J", leg.visible.mark_price),
        ("L", calculations.leg_mark_value(leg)),
        ("M", calculations.leg_pl_open(leg)),
        ("N", leg.visible.underlying_price),
        ("O", leg.visible.price_change),
        ("P", calculations.leg_total_delta(leg)),
        ("Q", leg.visible.delta),
        ("R", calculations.leg_total_theta(leg)),
        ("S", leg.visible.theta),
        ("T", leg.visible.itm),
        ("V", leg.visible.ev),
        ("W", leg.visible.dte),
    ]
    if leg.has_ev_column:
        cells.append(("U", calculations.leg_total_ev(leg)))
    if last:
        cells.extend(
            [
                ("G", calculations.block_net_entry(block)),
                ("I", block.capital),
                ("K", calculations.block_net_mark(block)),
                ("Y", calculations.block_total_pl(block)),
                ("Z", calculations.block_roi(block)),
                ("X", calculations.block_rolling_pl(block)),
            ]
        )
    for column, value in cells:
        if value is None:
            continue
        _write(sheet, f"{column}{row}", value, NUMFMT_BY_KEY[column])

    if leg.quote.raw:
        sheet[f"C{row}"].comment = Comment(leg.quote.raw, "quote")
    if last and not block.roll.is_empty():
        sheet[f"X{row}"].comment = Comment(f"Roll ledger: {block.roll.render()}", "otp-tracker")
    if last and block.net_divisor is not None:
        sheet[f"K{row}"].comment = Comment(f"otp-divisor={block.net_divisor:g}", "otp-tracker")

    for column, value in leg.extra_cells.items():
        # Fidelity cells with no structural slot (e.g. the stray X5 = 0.0 literal).
        if value is None:
            continue
        _write(sheet, f"{column}{row}", value, NUMFMT_BY_KEY.get(column))

    for column in leg.purple_cells:
        cell = sheet[f"{column}{row}"]
        cell.font = Font(color=PURPLE_RGB)


def _export_ledger(workbook: Workbook, ledger: MonthlyLedger) -> None:
    sheet = workbook.create_sheet(SHEET6)
    row = 2
    for month in ledger.months:
        _write(sheet, f"A{row}", month.month, "mmm yyyy")
        row += 1
        for entry in month.entries:
            _write(sheet, f"A{row}", entry.ticker)
            _write(sheet, f"B{row}", entry.pnl)
            if entry.extra is not None:
                _write(sheet, f"C{row}", entry.extra)
            row += 1
    sheet.column_dimensions["A"].width = 16.5
    sheet.column_dimensions["B"].width = 12.0
