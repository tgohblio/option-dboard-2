"""Number-format parity with the workbook (docs/spec.md §9).

Excel colour-codes numbers through number formats (``[Color10]``, ``[Red]``) and
Flet's DataTable2 has no per-cell format, so every cell is rendered as an
``ft.Text`` whose colour comes from these formatters.  ``format_cell`` returns
``(display_text, color)``.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import flet as ft

__all__ = ["PURPLE", "BLUE", "RED", "GREEN", "BLACK", "DEFAULT", "format_cell", "col_color"]

PURPLE = "#FF7E3794"  # source font override, reproduced verbatim
BLUE = ft.Colors.BLUE_900
RED = ft.Colors.RED_700
GREEN = ft.Colors.GREEN_700
BLACK = ft.Colors.BLACK
DEFAULT = ft.Colors.BLACK

EMPTY = "—"


def col_color(value: float) -> str:
    """Sign colour used by every workbook number format."""
    if value > 0:
        return BLUE
    if value < 0:
        return RED
    return BLACK


def format_cell(value: Any, fmt_id: str, purple: bool = False) -> tuple[str, str]:
    """Render ``value`` with the Excel number format identified by ``fmt_id``."""
    if value is None:
        return EMPTY, (PURPLE if purple else DEFAULT)
    if isinstance(value, str) and not value.strip():
        # blank input cell (e.g. block 1's empty Entry Price) — stay blank like Excel
        return "", (PURPLE if purple else DEFAULT)

    color = PURPLE if purple else None

    if fmt_id == "money":
        # [Color10]#,###.00;([Red]#,###.00);[Black]0.0
        number = float(value)
        if number > 0:
            return f"{number:,.2f}", color or BLUE
        if number < 0:
            return f"({abs(number):,.2f})", color or RED
        return "0.0", color or BLACK

    if fmt_id == "money_simple":
        # #,##0.00 — negative sign in front, no parentheses
        number = float(value)
        text = f"{number:,.2f}"
        if color:
            return text, color
        return text, (RED if number < 0 else DEFAULT)

    if fmt_id == "qty":
        number = float(value)
        if number == 0:
            return "0.0", color or BLACK
        rounded = int(round(number)) if number == int(number) else number
        text = f"{rounded:,}" if isinstance(rounded, int) else f"{rounded:,.2f}"
        if color:
            return text, color
        if number < 0:
            return f"({text.lstrip('-')})", RED
        return text, BLUE

    if fmt_id == "dollar":
        number = float(value)
        text = f"${number:,.2f}"
        if color:
            return text, color
        return text, (RED if number < 0 else (BLUE if number > 0 else DEFAULT))

    if fmt_id == "percent":
        number = float(value)
        text = f"{number * 100:.2f}%"
        if color:
            return text, color
        return text, col_color(number)

    if fmt_id == "num":
        return f"{float(value):g}", color or DEFAULT

    if fmt_id == "date":
        if isinstance(value, date):
            return value.strftime("%-m/%-d/%Y"), color or DEFAULT
        return str(value), color or DEFAULT

    if fmt_id == "bool":
        truthy = bool(value)
        return ("YES", color or GREEN) if truthy else ("NO", color or RED)

    # fmt_id == "text" and anything unknown
    return str(value), color or DEFAULT
