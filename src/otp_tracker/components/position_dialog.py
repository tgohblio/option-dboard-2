"""Add / edit position dialog (docs/spec.md §11.4).

Closes declaratively with ``dialog.open = False`` and rebinds the grid in the same
handler — the verified Flet 1.0 recipe that keeps the co-batched page update
(pitfall B).
"""

from __future__ import annotations

from datetime import date, datetime

import flet as ft

from .. import symbology
from ..models import Leg, PositionBlock

__all__ = ["open_position_dialog"]


class _ValidationError(ValueError):
    pass


def _parse_float(text: str, label: str, *, required: bool = False) -> float | None:
    cleaned = (text or "").strip().replace(",", "")
    if not cleaned:
        if required:
            raise _ValidationError(f"{label} is required")
        return None
    try:
        return float(cleaned)
    except ValueError as exc:
        raise _ValidationError(f"{label} must be a number (got {text!r})") from exc


def _parse_date(text: str) -> date | None:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise _ValidationError(f"Entry date must look like M/D/YYYY (got {text!r})")


def _num_filter() -> ft.InputFilter:
    return ft.InputFilter(allow=True, regex_string=r"[+-]?[0-9]*\.?[0-9]*")


def open_position_dialog(
    page: ft.Page,
    state,
    *,
    mode: str = "add_leg",
    leg: Leg | None = None,
    block: PositionBlock | None = None,
) -> ft.AlertDialog:
    """Open the position form in ``add_leg``, ``add_block`` or ``edit`` mode."""
    editing = mode == "edit"
    target_block = block or (state.block_of(leg) if leg else None)
    if target_block is None and state.blocks():
        target_block = state.blocks()[-1]

    action_field = ft.TextField(
        key="f-action", label="Trade Action / Notif. #", value=(leg.action_note or "") if leg else "", width=200
    )
    strategy_field = ft.Dropdown(
        key="f-strategy",
        label="Strategy",
        width=180,
        editable=True,
        options=[
            ft.DropdownOption(key="", text="(blank)"),
            ft.DropdownOption(key="Diagonal", text="Diagonal"),
            ft.DropdownOption(key="BWB", text="BWB"),
        ],
        value=(target_block.strategy if target_block and target_block.strategy else "") or "",
    )
    symbol_field = ft.TextField(
        key="f-symbol", label="OPRA symbol", hint_text=".AMD220715C85", value=(leg.symbol or "") if leg else "", width=200
    )
    date_field = ft.TextField(
        key="f-date",
        label="Entry date",
        hint_text="M/D/YYYY",
        width=140,
        value=leg.entry_date.strftime("%-m/%-d/%Y") if leg and leg.entry_date else "",
    )
    qty_field = ft.TextField(
        key="f-qty",
        label="Qty",
        width=100,
        input_filter=_num_filter(),
        value=f"{leg.qty:g}" if leg else "",
        helper="negative = short",
    )
    price_field = ft.TextField(
        key="f-entry-price",
        label="Entry price",
        width=120,
        input_filter=_num_filter(),
        value="" if not leg or leg.entry_price is None else f"{leg.entry_price:g}",
        helper="blank allowed",
    )
    capital_field = ft.TextField(
        key="f-capital",
        label="Block capital",
        width=140,
        input_filter=_num_filter(),
        value="" if not target_block or target_block.capital is None else f"{target_block.capital:g}",
    )
    divisor_field = ft.TextField(
        key="f-net-divisor",
        label="Net MARK divisor override",
        width=210,
        input_filter=_num_filter(),
        value="" if not target_block or target_block.net_divisor is None else f"{target_block.net_divisor:g}",
        helper="blank = last leg Qty (source K13 uses 1000)",
    )
    error_text = ft.Text("", color=ft.Colors.RED_700, size=12)

    title = {"edit": "Edit position", "add_block": "Add strategy block"}.get(mode, "Add leg")
    if editing and leg is not None:
        title = f"Edit position — row {leg.sheet_row}"

    dialog = ft.AlertDialog(
        key="position-dialog",
        modal=True,
        title=ft.Text(title),
        content=ft.Container(
            width=520,
            content=ft.Column(
                tight=True,
                spacing=10,
                controls=[
                    ft.Row([action_field, strategy_field], spacing=10),
                    ft.Row([symbol_field, date_field], spacing=10),
                    ft.Row([qty_field, price_field, capital_field], spacing=10),
                    divisor_field,
                    error_text,
                ],
            ),
        ),
        actions=[
            ft.TextButton("Cancel", key="cancel-position", on_click=lambda e: _close(dialog, page)),
            ft.FilledButton("Save", key="save-position", on_click=lambda e: _save()),
        ],
    )

    def _close(dlg, pg):
        dlg.open = False
        pg.update()

    def _save() -> None:
        try:
            symbol_text = (symbol_field.value or "").strip()
            parsed_symbol = symbology.parse(symbol_text or None)  # raises on bad text
            qty = _parse_float(qty_field.value, "Qty", required=True)
            assert qty is not None
            entry_price = _parse_float(price_field.value, "Entry price")
            capital = _parse_float(capital_field.value, "Block capital")
            divisor = _parse_float(divisor_field.value, "Net MARK divisor")
            entry_date = _parse_date(date_field.value)
        except symbology.SymbolParseError as exc:
            symbol_field.error = str(exc)
            error_text.value = "Fix the highlighted fields and try again."
            page.update()
            return
        except _ValidationError as exc:
            error_text.value = str(exc)
            page.update()
            return

        strategy_value = (strategy_field.value or strategy_field.text or "").strip() or None
        note = (action_field.value or "").strip() or None

        if editing and leg is not None and target_block is not None:
            leg.action_note = note
            leg.symbol = parsed_symbol.raw if parsed_symbol else None
            leg.entry_date = entry_date
            leg.qty = qty
            leg.entry_price = entry_price
            if leg.sheet_row == target_block.row_end:
                target_block.capital = capital
                target_block.net_divisor = divisor
            state.touch("Position updated")
        elif mode == "add_block" or not state.blocks():
            row = state.next_row()
            new_leg = Leg(
                sheet_row=row,
                symbol=parsed_symbol.raw if parsed_symbol else None,
                entry_date=entry_date,
                qty=qty,
                entry_price=entry_price,
                action_note=note,
            )
            new_block = PositionBlock(
                row_start=row,
                row_end=row,
                strategy=strategy_value,
                capital=capital,
                net_divisor=divisor,
                legs=[new_leg],
            )
            state.portfolio.blocks.append(new_block)
            state.touch(f"Added block at row {row}")
        else:
            row = state.next_row()
            new_leg = Leg(
                sheet_row=row,
                symbol=parsed_symbol.raw if parsed_symbol else None,
                entry_date=entry_date,
                qty=qty,
                entry_price=entry_price,
                action_note=note,
            )
            state.append_leg(new_leg, target_block)
            state.touch(f"Added leg at row {row}")

        dialog.open = False  # declarative close keeps the co-batched grid update
        state.refresh()

    page.show_dialog(dialog)
    return dialog
