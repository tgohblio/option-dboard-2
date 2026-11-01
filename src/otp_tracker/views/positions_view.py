"""Positions view — the ``Open Trades`` screen (docs/spec.md §11.1).

Non-scrolling title band + toolbar + sticky DataTable2 grid + totals strip.  Every
mutation recomputes, rebinds the grid collections and schedules a debounced save.
"""

from __future__ import annotations

import asyncio

import flet as ft
import flet_datatable2 as fdt

from .. import calculations, excel_io
from ..app_state import get_state
from ..components.position_dialog import open_position_dialog
from ..components.position_table import build_columns, build_rows
from ..components.quote_dialog import open_quote_dialog
from ..components.roll_dialog import open_roll_dialog
from ..formatting import format_cell

# typo preserved from source workbook
TITLE = "OTP PORFOLIO TRACKER - OPEN TRADES"


def _confirm(page: ft.Page, title: str, message: str, on_confirm) -> None:
    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(title),
        content=ft.Text(message, size=13),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: _close(dialog, page)),
            ft.FilledButton("Continue", key="confirm-ok", on_click=lambda e: _ok()),
        ],
    )

    def _close(dlg, pg):
        dlg.open = False
        pg.update()

    def _ok() -> None:
        dialog.open = False
        on_confirm()

    page.show_dialog(dialog)


def build_positions_view(page: ft.Page):
    state = get_state()

    status_text = ft.Text(state.status, key="status-strip", size=12, color=ft.Colors.GREY_800)
    total_delta = ft.Text("", key="total-delta", size=13, weight=ft.FontWeight.BOLD)
    total_theta = ft.Text("", key="total-theta", size=13, weight=ft.FontWeight.BOLD)
    total_pl = ft.Text("", key="total-pl", size=13, weight=ft.FontWeight.BOLD)
    total_capital = ft.Text("", key="total-capital", size=13, weight=ft.FontWeight.BOLD)

    grid = fdt.DataTable2(
        key="otp-table",
        columns=build_columns(state.show_market),
        rows=build_rows(state.portfolio, state.show_market),
        fixed_top_rows=1,
        fixed_left_columns=3,
        min_width=2520,
        data_row_height=34,
        heading_row_height=40,
        # DataTable's default 24px horizontal margin per cell clips the 68-96px
        # numeric columns; a tight margin keeps "(1,170.00)" fully visible.
        horizontal_margin=6,
        column_spacing=6,
        divider_thickness=0.5,
        visible_horizontal_scroll_bar=True,
        empty=ft.Text("No open trades — use Add Leg."),
        expand=True,
    )

    totals_map = {"delta": total_delta, "theta": total_theta, "pl": total_pl, "capital": total_capital}
    state.bind(grid, status_text, totals_map)

    def refresh_totals() -> None:
        delta_text, delta_color = format_cell(state.total_delta(), "money")
        theta_text, theta_color = format_cell(state.total_theta(), "money")
        pl_text, pl_color = format_cell(state.total_pl(), "money")
        cap_text, _ = format_cell(state.total_capital(), "money_simple")
        total_delta.value, total_delta.color = delta_text, delta_color
        total_theta.value, total_theta.color = theta_text, theta_color
        total_pl.value, total_pl.color = pl_text, pl_color
        total_capital.value = cap_text

    refresh_totals()

    # ------------------------------------------------------------------ events
    def on_refresh(e) -> None:
        state.refresh(
            f"Recomputed {len(state.legs())} legs / {len(state.blocks())} blocks in Python"
        )

    def on_toggle_market(e) -> None:
        state.show_market = not state.show_market
        toggle_button.content = (
            "Hide market data (N–W)" if state.show_market else "Show market data (N–W)"
        )
        state.refresh("Market data " + ("shown" if state.show_market else "hidden"))

    def on_add_leg(e) -> None:
        open_position_dialog(page, state, mode="add_leg")

    def on_add_block(e) -> None:
        open_position_dialog(page, state, mode="add_block")

    def _require_selection(action: str):
        if state.selected_leg is None:
            state.set_status(f"Select a row first, then {action}.")
            page.update()
            return None
        return state.selected_leg

    def on_edit(e) -> None:
        leg = _require_selection("edit")
        if leg is None:
            return
        open_position_dialog(page, state, mode="edit", leg=leg)

    def on_quote(e) -> None:
        leg = _require_selection("open the quote")
        if leg is None:
            return
        open_quote_dialog(page, state, leg)

    def on_roll(e) -> None:
        leg = _require_selection("open the roll ledger")
        if leg is None:
            return
        block = state.block_of(leg)
        if block is None:
            state.set_status("Selected row has no block.")
            page.update()
            return
        open_roll_dialog(page, state, block)

    def on_delete(e) -> None:
        leg = _require_selection("delete")
        if leg is None:
            return

        def do_delete() -> None:
            row = leg.sheet_row
            state.remove_leg(leg)
            state.touch(f"Deleted row {row}")
            state.refresh()

        _confirm(
            page,
            "Delete position",
            f"Delete leg at row {leg.sheet_row} ({leg.symbol or 'blank OPRA'})? "
            "The block is removed when its last leg is deleted.",
            do_delete,
        )

    def on_save(e) -> None:
        page.run_task(save_clicked)

    async def save_clicked() -> None:
        ok = await state.save_async()
        if ok:
            state.refresh(f"Saved to {state.store.path}")

    def on_import(e) -> None:
        page.run_task(import_clicked)

    async def import_clicked() -> None:
        picker = ft.FilePicker()
        files = await picker.pick_files(
            dialog_title="Import OTPTracker workbook",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["xlsx"],
            allow_multiple=False,
        )
        if not files:
            return
        selected = files[0]
        path = getattr(selected, "path", None)
        if not path:
            state.set_status("Import cancelled: no local path (browser uploads are not supported).")
            page.update()
            return

        def do_import() -> None:
            page.run_task(run_import, path)

        _confirm(
            page,
            "Replace current data?",
            f"Import {path}? This replaces the current portfolio and ledger in the app store.",
            do_import,
        )

    async def run_import(path: str) -> None:
        state.set_status(f"Importing {path}…")
        page.update()
        try:
            result = await asyncio.to_thread(excel_io.import_workbook, path)
        except excel_io.WorkbookImportError as exc:
            state.set_status(f"Import failed: {exc}")
            page.update()
            return
        state.portfolio = result.portfolio
        state.ledger = result.ledger
        state.source = result.source
        state.warnings = list(result.warnings)
        state.selected_leg = None
        state.touch()
        await state.save_async(silent=True)
        message = f"Imported {len(state.legs())} legs / {len(state.blocks())} blocks"
        if result.warnings:
            message += f" — {result.warnings[0]}"
        state.refresh(message)

    def on_export(e) -> None:
        page.run_task(export_clicked)

    async def export_clicked() -> None:
        picker = ft.FilePicker()
        path = await picker.save_file(
            dialog_title="Export OTPTracker workbook",
            file_name="OTPTracker.xlsx",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["xlsx"],
        )
        if not path:
            return
        try:
            written = await asyncio.to_thread(
                excel_io.export_workbook, path, state.portfolio, state.ledger
            )
        except OSError as exc:
            state.set_status(f"Export failed: {exc}")
            page.update()
            return
        state.set_status(f"Exported {written}")
        page.update()

    toggle_button = ft.Button(
        "Hide market data (N–W)" if state.show_market else "Show market data (N–W)",
        key="toggle-market",
        icon=ft.Icons.SWAP_HORIZ,
        on_click=on_toggle_market,
    )

    toolbar = ft.Row(
        wrap=True,
        spacing=8,
        run_spacing=8,
        controls=[
            ft.FilledButton("Add Leg", key="add-leg", icon=ft.Icons.ADD, on_click=on_add_leg),
            ft.Button("Add Block", key="add-block", icon=ft.Icons.LAYERS, on_click=on_add_block),
            ft.Button("Edit…", key="edit-row", icon=ft.Icons.EDIT, on_click=on_edit),
            ft.Button("Delete…", key="delete-row", icon=ft.Icons.DELETE_OUTLINE, on_click=on_delete),
            ft.Button("Quote…", key="quote-row", icon=ft.Icons.COMMENT, on_click=on_quote),
            ft.Button("Roll P/L…", key="roll-row", icon=ft.Icons.TUNE, on_click=on_roll),
            ft.VerticalDivider(width=1),
            toggle_button,
            ft.Button("Refresh", key="refresh", icon=ft.Icons.REFRESH, on_click=on_refresh),
            ft.Button("Import .xlsx…", key="import-xlsx", icon=ft.Icons.UPLOAD_FILE, on_click=on_import),
            ft.Button("Export .xlsx…", key="export-xlsx", icon=ft.Icons.DOWNLOAD, on_click=on_export),
            ft.FilledButton("Save", key="save", icon=ft.Icons.SAVE, on_click=on_save),
        ],
    )

    as_of = state.portfolio.as_of_date
    as_of_label = f"As of {as_of.strftime('%-m/%-d/%Y')}" if as_of else "As of —"

    title_band = ft.Container(
        padding=ft.Padding.only(left=4, right=4, top=2, bottom=2),
        content=ft.Column(
            spacing=2,
            controls=[
                ft.Text(TITLE, key="app-title", size=20, weight=ft.FontWeight.BOLD),
                ft.Row(
                    spacing=24,
                    controls=[
                        ft.Text("Entry Data", size=12, weight=ft.FontWeight.BOLD),
                        ft.Text("Market Data", size=12, weight=ft.FontWeight.BOLD),
                        ft.Text(as_of_label, size=12, color=ft.Colors.GREY_700),
                    ],
                ),
            ],
        ),
    )

    totals_strip = ft.Container(
        padding=ft.Padding.symmetric(horizontal=6, vertical=6),
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        border_radius=4,
        content=ft.Row(
            spacing=22,
            wrap=True,
            controls=[
                ft.Text("TOTAL", size=13, weight=ft.FontWeight.BOLD),
                ft.Text("Total Delta", size=12, color=ft.Colors.GREY_700),
                total_delta,
                ft.Text("Total Theta", size=12, color=ft.Colors.GREY_700),
                total_theta,
                ft.Text("Total P/L", size=12, color=ft.Colors.GREY_700),
                total_pl,
                ft.Text("Capital", size=12, color=ft.Colors.GREY_700),
                total_capital,
            ],
        ),
    )

    return ft.Column(
        expand=True,
        spacing=6,
        controls=[
            title_band,
            toolbar,
            ft.Container(content=grid, expand=True),
            status_text,
            totals_strip,
        ],
    )
