"""The ``Open Trades`` grid: column catalog + row builders.

Every computed cell is rendered through :func:`otp_tracker.formatting.format_cell`
so the grid keeps Excel's colour-coded number formats.  Rows are always returned
as fresh lists and assigned by the caller (``grid.rows = build_rows(...)``) — never
appended in place (Flet pitfall A, docs/spec.md §11.5).

Editing is dialog-driven: the grid shows the sheet verbatim and each row carries a
selection handler, a quote button (column C) and the block roll button (column X).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import flet as ft
import flet_datatable2 as fdt

from .. import calculations
from ..formatting import format_cell
from ..models import Leg, Portfolio, PositionBlock

__all__ = [
    "ColumnSpec",
    "COLUMNS",
    "MARKET_KEYS",
    "build_columns",
    "build_rows",
    "TableCallbacks",
    "short_label",
]

MARKET_KEYS = frozenset("NOPQRSTUVW")  # the workbook's outline group


@dataclass(frozen=True)
class ColumnSpec:
    key: str
    header: str  # short single-line label
    tooltip: str  # full stored header
    fmt: str
    width: int
    numeric: bool = False

    @property
    def is_market(self) -> bool:
        return self.key in MARKET_KEYS


#: Column catalog A..Z (docs/spec.md §2.2). Widths are the spec's px targets.
COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("A", "Notif.", "Trade Action / Notif. #", "text", 92),
    ColumnSpec("B", "Strategy", "Strategy", "text", 78),
    ColumnSpec("C", "OPRA", "OPRA", "text", 136),
    ColumnSpec("D", "Entry Date", "Entry Date", "date", 92),
    ColumnSpec("E", "Qty", "Qty (signed: negative = short)", "qty", 84, True),
    ColumnSpec("F", "Entry Price", "Entry Price (blank allowed)", "money_simple", 88, True),
    ColumnSpec("G", "Net Entry", "Net Entry Price = SUM(Entry Value) / last leg Qty", "money_simple", 92, True),
    ColumnSpec("H", "Entry Value", "Entry Value = Entry Price × Qty", "money", 100, True),
    ColumnSpec("I", "Capital", "Capital / Buying Power (Max Loss) — one per block", "money_simple", 132, True),
    ColumnSpec("J", "Mark", "MARK Price (per share)", "money", 88, True),
    ColumnSpec("K", "Net Mark", "Net MARK Price = SUM(MARK Value) / divisor", "money", 96, True),
    ColumnSpec("L", "Mark Value", "MARK Value = MARK Price × Qty", "money", 100, True),
    ColumnSpec("M", "P/L Open", "P/L Open = MARK Value − Entry Value", "money", 100, True),
    ColumnSpec("N", "Underlying", "Underlying Price", "num", 100, True),
    ColumnSpec("O", "Chg", "Price Change", "num", 82, True),
    ColumnSpec("P", "Δ Total", "Total Delta = Delta per share × Qty", "money", 92, True),
    ColumnSpec("Q", "Δ/shr", "Delta per share", "num", 88, True),
    ColumnSpec("R", "Θ Total", "Total Theta = Theta per share × Qty", "money", 92, True),
    ColumnSpec("S", "Θ/shr", "Theta per share", "num", 88, True),
    ColumnSpec("T", "ITM", "In The Money (True/False in the workbook)", "bool", 52),
    ColumnSpec("U", "EV Total", "Total Extrinsic Value = EV per share × Qty", "dollar", 104, True),
    ColumnSpec("V", "EV/shr", "Extrinsic Value per share", "num", 82, True),
    ColumnSpec("W", "DTE", "Remaining Days To Expiry", "num", 62, True),
    ColumnSpec("X", "Roll P/L", "Rolling P/L (human-maintained ledger)", "money_simple", 100, True),
    ColumnSpec("Y", "Total P/L", "Total P/L = SUM(P/L Open) + Rolling P/L", "money", 102, True),
    ColumnSpec("Z", "% ROI", "ROI = Total P/L ÷ Capital", "percent", 82, True),
)

_SHORT = {column.key: column.header for column in COLUMNS}


def short_label(key: str) -> str:
    return _SHORT.get(key, key)


def build_columns(show_market: bool = True) -> list[fdt.DataColumn2]:
    """Rebuild the DataColumn2 list; hides N–W when market data is toggled off."""
    columns: list[fdt.DataColumn2] = []
    for spec in COLUMNS:
        if not show_market and spec.is_market:
            continue
        columns.append(
            fdt.DataColumn2(
                label=ft.Text(spec.header, size=11, weight=ft.FontWeight.BOLD, no_wrap=True, overflow=ft.TextOverflow.CLIP),
                tooltip=spec.tooltip,
                numeric=spec.numeric,
                fixed_width=spec.width,
            )
        )
    return columns


@dataclass
class TableCallbacks:
    on_select: Callable[[Leg], None]
    on_quote: Callable[[Leg], None]
    on_roll: Callable[[PositionBlock], None]


class _DefaultCallbacks(TableCallbacks):
    """Fallbacks used when a view rebinds the grid without passing callbacks."""

    def __init__(self) -> None:  # noqa: D107 - simple wiring
        super().__init__(self._select, self._quote, self._roll)

    def _state(self):
        from ..app_state import get_state

        return get_state()

    def _page(self) -> ft.Page:
        return ft.context.page

    def _select(self, leg: Leg) -> None:
        state = self._state()
        state.selected_leg = leg
        state.set_status(f"Selected row {leg.sheet_row} — use Edit… or Quote…")
        state.refresh()

    def _quote(self, leg: Leg) -> None:
        from .quote_dialog import open_quote_dialog

        open_quote_dialog(self._page(), self._state(), leg)

    def _roll(self, block: PositionBlock) -> None:
        from .roll_dialog import open_roll_dialog

        open_roll_dialog(self._page(), self._state(), block)


DEFAULT_CALLBACKS = _DefaultCallbacks()


def _cell_text(
    value,
    fmt: str,
    *,
    purple: bool = False,
    bold: bool = False,
    color: str | None = None,
    size: int = 12,
) -> ft.Text:
    text, computed_color = format_cell(value, fmt, purple=purple)
    return ft.Text(
        text,
        color=color or computed_color,
        size=size,
        weight=ft.FontWeight.BOLD if bold else None,
        no_wrap=True,
        overflow=ft.TextOverflow.CLIP,
    )


def _quote_cell(leg: Leg, on_click) -> ft.Control:
    quote = leg.quote
    placeholder = quote.is_placeholder
    icon_color = ft.Colors.GREY_500 if placeholder else ft.Colors.BLUE_700
    tooltip = (
        "Quote lookup failed — paste a fresh quote blob"
        if placeholder
        else f"{quote.info} · mark {quote.mark_price:g} · Δ {quote.delta:g} · Θ {quote.theta:g}"
    )
    symbol = leg.symbol or ""
    return ft.Row(
        spacing=2,
        tight=True,
        controls=[
            ft.Text(symbol, size=11, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, width=112),
            ft.Container(
                key=f"quote-btn-{leg.sheet_row}",
                content=ft.Icon(ft.Icons.COMMENT_OUTLINED, size=13, color=icon_color),
                tooltip=tooltip,
                on_click=lambda e, leg=leg: on_click(leg),
                padding=0,
                width=16,
                height=16,
                alignment=ft.Alignment.CENTER,
            ),
        ],
    )


def _roll_cell(block: PositionBlock, on_click, last: bool) -> ft.Control:
    if not last:
        return ft.Text("", size=12)
    value = calculations.block_rolling_pl(block)
    text = _cell_text(value, "money_simple", bold=True)
    text.tooltip = f"Roll ledger: {block.roll.render()}\nClick to edit pairs and credits."
    return ft.Container(
        key=f"roll-btn-{block.row_start}",
        content=ft.Row(
            spacing=2,
            tight=True,
            controls=[
                text,
                ft.Icon(ft.Icons.TUNE, size=12, color=ft.Colors.BLUE_700),
            ],
        ),
        on_click=lambda e, block=block: on_click(block),
        padding=0,
    )


def build_rows(
    portfolio: Portfolio,
    show_market: bool = True,
    callbacks: TableCallbacks | None = None,
) -> list[ft.DataRow]:
    """One DataRow per leg, in sheet order, with block banding and bold block cells."""
    callbacks = callbacks or DEFAULT_CALLBACKS
    rows: list[ft.DataRow] = []
    for block_index, block in enumerate(portfolio.blocks):
        net_entry = calculations.block_net_entry(block)
        net_mark = calculations.block_net_mark(block)
        total_pl = calculations.block_total_pl(block)
        roi = calculations.block_roi(block)
        band = "#FFF3F6FA" if block_index % 2 else None
        for leg in block.legs:
            last = leg.sheet_row == block.row_end
            first = leg.sheet_row == block.row_start
            purple = set(leg.purple_cells)

            cells: list[ft.DataCell] = [
                ft.DataCell(_cell_text(leg.action_note or "", "text")),
                ft.DataCell(
                    _cell_text(block.strategy or "", "text", bold=first and bool(block.strategy))
                    if first
                    else ft.Text("", size=12)
                ),
                ft.DataCell(_quote_cell(leg, callbacks.on_quote)),
                ft.DataCell(_cell_text(leg.entry_date, "date")),
                ft.DataCell(_cell_text(leg.qty, "qty", purple="E" in purple)),
                ft.DataCell(
                    _cell_text(
                        leg.entry_price if leg.entry_price is not None else "",
                        "money_simple",
                        purple="F" in purple,
                    )
                ),
                ft.DataCell(
                    _cell_text(net_entry, "money_simple", purple="G" in purple, bold=last)
                    if last
                    else ft.Text("", size=12)
                ),
                ft.DataCell(_cell_text(calculations.leg_entry_value(leg), "money", purple="H" in purple)),
                ft.DataCell(
                    _cell_text(block.capital, "money_simple", purple="I" in purple, bold=last)
                    if last
                    else ft.Text("", size=12)
                ),
                ft.DataCell(_cell_text(leg.visible.mark_price, "money", purple="J" in purple)),
                ft.DataCell(
                    _cell_text(net_mark, "money", purple="K" in purple, bold=last)
                    if last
                    else ft.Text("", size=12)
                ),
                ft.DataCell(_cell_text(calculations.leg_mark_value(leg), "money", purple="L" in purple)),
                ft.DataCell(_cell_text(calculations.leg_pl_open(leg), "money", purple="M" in purple)),
            ]

            if show_market:
                cells.extend(
                    [
                        ft.DataCell(_cell_text(leg.visible.underlying_price, "num", purple="N" in purple)),
                        ft.DataCell(_cell_text(leg.visible.price_change, "num", purple="O" in purple)),
                        ft.DataCell(
                            _cell_text(calculations.leg_total_delta(leg), "money", purple="P" in purple)
                        ),
                        ft.DataCell(_cell_text(leg.visible.delta, "num", purple="Q" in purple)),
                        ft.DataCell(
                            _cell_text(calculations.leg_total_theta(leg), "money", purple="R" in purple)
                        ),
                        ft.DataCell(_cell_text(leg.visible.theta, "num", purple="S" in purple)),
                        ft.DataCell(_cell_text(leg.visible.itm, "bool", purple="T" in purple)),
                        ft.DataCell(
                            _cell_text(calculations.leg_total_ev(leg), "dollar", purple="U" in purple)
                            if leg.has_ev_column
                            else ft.Text("", size=12)
                        ),
                        ft.DataCell(_cell_text(leg.visible.ev, "num", purple="V" in purple)),
                        ft.DataCell(_cell_text(leg.visible.dte, "num", purple="W" in purple)),
                    ]
                )

            cells.extend(
                [
                    ft.DataCell(_roll_cell(block, callbacks.on_roll, last)),
                    ft.DataCell(
                        _cell_text(total_pl, "money", purple="Y" in purple, bold=last)
                        if last
                        else ft.Text("", size=12)
                    ),
                    ft.DataCell(
                        _cell_text(roi, "percent", purple="Z" in purple, bold=last)
                        if last
                        else ft.Text("", size=12)
                    ),
                ]
            )

            rows.append(
                ft.DataRow(
                    key=f"row-{leg.sheet_row}",
                    cells=cells,
                    color=band,
                    selected=False,
                    on_select_change=lambda e, leg=leg: callbacks.on_select(leg),
                )
            )
    return rows


def block_summary_rows(portfolio: Portfolio) -> list[tuple[str, str, int, int, float | None, float | None, float, float, float | None]]:
    """Helper for the Summary view: one tuple per block."""
    summary = []
    for block in portfolio.blocks:
        summary.append(
            (
                block.label,
                f"{block.row_start}–{block.row_end}",
                len(block.legs),
                block.row_end,
                block.capital,
                calculations.block_net_entry(block),
                calculations.block_rolling_pl(block),
                calculations.block_total_pl(block),
                calculations.block_roi(block),
            )
        )
    return summary
