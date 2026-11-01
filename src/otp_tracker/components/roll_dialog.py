"""Roll ledger dialog — edit column X as structured ``(new − closed)`` pairs.

The workbook keeps rolls as a hand-written literal, e.g.
``=((1.34-1.55)+(2.46-3)+…+1.07)*100``; this dialog parses that text back into
pairs + credits, lets the user add/remove pairs, and re-renders the literal on save
(docs/spec.md §5.3, §11.4).
"""

from __future__ import annotations

import flet as ft

from ..models import PositionBlock, RollLedger, RollPair

__all__ = ["open_roll_dialog"]


def open_roll_dialog(page: ft.Page, state, block: PositionBlock) -> ft.AlertDialog:
    pairs: list[dict[str, ft.TextField]] = []
    preview = ft.Text("", size=12, color=ft.Colors.GREY_800)
    error = ft.Text("", size=12, color=ft.Colors.RED_700)
    literal_zero = ft.Checkbox(label="Cell is the literal 0 (no roll ledger)", value=block.roll.literal_zero)

    credits_field = ft.TextField(
        key="roll-credits",
        label="Unpaired credit terms",
        hint_text="e.g. 1.07, -0.5",
        width=260,
        value=", ".join(f"{credit:g}" for credit in block.roll.credits),
    )

    pairs_column = ft.Column(spacing=6, tight=True, controls=[])
    paste_field = ft.TextField(
        key="roll-paste",
        label="Paste Excel expression",
        hint_text="=((1.34-1.55)+(2.46-3)+1.07)*100",
        width=520,
    )

    def current_ledger() -> RollLedger:
        parsed_pairs: list[RollPair] = []
        for index, pair in enumerate(pairs):
            new_text = (pair["new"].value or "").strip()
            closed_text = (pair["closed"].value or "").strip()
            if not new_text and not closed_text:
                continue
            if not new_text or not closed_text:
                raise ValueError(f"Roll #{index + 1} needs both prices")
            parsed_pairs.append(RollPair(new_px=float(new_text), closed_px=float(closed_text)))
        credits: list[float] = []
        for token in (credits_field.value or "").replace(";", ",").split(","):
            token = token.strip()
            if token:
                credits.append(float(token))
        return RollLedger(pairs=parsed_pairs, credits=credits, literal_zero=bool(literal_zero.value))

    def update_preview() -> None:
        try:
            ledger = current_ledger()
        except ValueError as exc:
            preview.value = f"⚠ {exc}"
            preview.color = ft.Colors.RED_700
        else:
            preview.value = f"{ledger.render()}   →   {ledger.value():,.2f}"
            preview.color = ft.Colors.GREY_800

    def rebuild_pairs() -> None:
        rows: list[ft.Control] = []
        for index, pair in enumerate(pairs):
            rows.append(
                ft.Row(
                    spacing=6,
                    controls=[
                        ft.Text(f"#{index + 1}", width=28, size=12, color=ft.Colors.GREY_600),
                        pair["new"],
                        ft.Text("−", width=14, size=12),
                        pair["closed"],
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            icon_color=ft.Colors.RED_400,
                            tooltip="Remove this roll",
                            on_click=lambda e, index=index: remove_pair(index),
                        ),
                    ],
                )
            )
        if not rows:
            rows.append(ft.Text("No roll events yet.", size=12, color=ft.Colors.GREY_600))
        pairs_column.controls = rows
        update_preview()

    def add_pair(new_px: float | str = "", closed_px: float | str = "") -> None:
        pairs.append(
            {
                "new": ft.TextField(value=str(new_px), width=110, dense=True, label="New", on_change=lambda e: update_preview()),
                "closed": ft.TextField(
                    value=str(closed_px), width=110, dense=True, label="Closed", on_change=lambda e: update_preview()
                ),
            }
        )
        rebuild_pairs()

    def remove_pair(index: int) -> None:
        if 0 <= index < len(pairs):
            del pairs[index]
        rebuild_pairs()
        page.update()

    def parse_expression(e=None) -> None:
        text = (paste_field.value or "").strip()
        if not text:
            error.value = "Paste an Excel expression first"
            page.update()
            return
        try:
            ledger = RollLedger.from_expression(text)
        except ValueError as exc:
            error.value = f"Cannot parse: {exc}"
            page.update()
            return
        error.value = ""
        pairs.clear()
        literal_zero.value = ledger.literal_zero
        credits_field.value = ", ".join(f"{credit:g}" for credit in ledger.credits)
        for pair in ledger.pairs:
            pairs.append(
                {
                    "new": ft.TextField(value=f"{pair.new_px:g}", width=110, dense=True, label="New", on_change=lambda e: update_preview()),
                    "closed": ft.TextField(
                        value=f"{pair.closed_px:g}", width=110, dense=True, label="Closed", on_change=lambda e: update_preview()
                    ),
                }
            )
        rebuild_pairs()
        page.update()

    for pair in block.roll.pairs:
        pairs.append(
            {
                "new": ft.TextField(value=f"{pair.new_px:g}", width=110, dense=True, label="New", on_change=lambda e: update_preview()),
                "closed": ft.TextField(
                    value=f"{pair.closed_px:g}", width=110, dense=True, label="Closed", on_change=lambda e: update_preview()
                ),
            }
        )
    rebuild_pairs()

    dialog = ft.AlertDialog(
        key="roll-dialog",
        modal=True,
        title=ft.Text(f"Rolling P/L — block rows {block.row_start}–{block.row_end}"),
        content=ft.Container(
            width=620,
            content=ft.Column(
                tight=True,
                spacing=10,
                controls=[
                    ft.Text(
                        "Each pair adds (new price − closed price); the total × 100 is column X.",
                        size=12,
                        color=ft.Colors.GREY_700,
                    ),
                    ft.Container(
                        height=220,
                        content=ft.Column(scroll=ft.ScrollMode.AUTO, controls=[pairs_column]),
                    ),
                    ft.Row(
                        spacing=8,
                        controls=[
                            ft.TextButton("Add roll", key="roll-add", icon=ft.Icons.ADD, on_click=lambda e: add_pair()),
                            credits_field,
                        ],
                    ),
                    ft.Row(
                        spacing=8,
                        controls=[paste_field, ft.Button("Parse", key="roll-parse", on_click=parse_expression)],
                    ),
                    literal_zero,
                    ft.Text("Preview", size=12, weight=ft.FontWeight.BOLD),
                    preview,
                    error,
                ],
            ),
        ),
        actions=[
            ft.TextButton("Cancel", key="roll-cancel", on_click=lambda e: _close()),
            ft.FilledButton("Save", key="roll-save", on_click=lambda e: _save()),
        ],
    )

    def _close() -> None:
        dialog.open = False
        page.update()

    def _save() -> None:
        try:
            ledger = current_ledger()
        except ValueError as exc:
            error.value = str(exc)
            page.update()
            return
        block.roll = ledger
        dialog.open = False
        state.touch(f"Roll ledger updated for rows {block.row_start}–{block.row_end}")
        state.refresh()

    page.show_dialog(dialog)
    return dialog
