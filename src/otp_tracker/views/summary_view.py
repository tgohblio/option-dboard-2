"""Portfolio Summary view — totals cards, per-block table, fidelity warnings."""

from __future__ import annotations

import re

import flet as ft
import flet_datatable2 as fdt

from .. import calculations
from ..app_state import get_state
from ..formatting import format_cell

# The importer's per-row divisor note ("Row 13: Net MARK divisor override 1000 …"),
# which the summary already reports as a block-level warning.
_ROW_DIVISOR_WARNING_RE = re.compile(r"Row (\d+):\s+Net MARK divisor override\b")


def _card(label: str, value: str, key: str, color: str | None = None) -> ft.Card:
    return ft.Card(
        content=ft.Container(
            padding=12,
            width=190,
            content=ft.Column(
                spacing=4,
                controls=[
                    ft.Text(label, size=11, color=ft.Colors.GREY_700),
                    ft.Text(value, key=key, size=18, weight=ft.FontWeight.BOLD, color=color),
                ],
            ),
        )
    )


def _column(label: str, width: int, numeric: bool = False) -> fdt.DataColumn2:
    return fdt.DataColumn2(
        label=ft.Text(
            label,
            size=12,
            weight=ft.FontWeight.BOLD,
            no_wrap=True,
            overflow=ft.TextOverflow.CLIP,
        ),
        fixed_width=width,
        numeric=numeric,
    )


def build_summary_view(page: ft.Page):
    state = get_state()

    delta_text, delta_color = format_cell(state.total_delta(), "money")
    theta_text, theta_color = format_cell(state.total_theta(), "money")
    open_text, open_color = format_cell(state.open_pl(), "money")
    total_text, total_color = format_cell(state.total_pl(), "money")
    capital_text, _ = format_cell(state.total_capital(), "money_simple")

    cards = ft.Row(
        wrap=True,
        spacing=12,
        run_spacing=12,
        controls=[
            _card("Total Delta", delta_text, "sum-total-delta", delta_color),
            _card("Total Theta", theta_text, "sum-total-theta", theta_color),
            _card("Open P/L Σ", open_text, "sum-open-pl", open_color),
            _card("Total P/L Σ", total_text, "sum-total-pl", total_color),
            _card("Capital Σ", capital_text, "sum-capital"),
            _card("Legs / Blocks", f"{len(state.legs())} / {len(state.blocks())}", "sum-counts"),
        ],
    )

    rows: list[ft.DataRow] = []
    for block in state.blocks():
        net_entry_text, _ = format_cell(calculations.block_net_entry(block), "money_simple")
        net_mark_text, _ = format_cell(calculations.block_net_mark(block), "money")
        roll_text, roll_color = format_cell(calculations.block_rolling_pl(block), "money_simple")
        pl_text, pl_color = format_cell(calculations.block_total_pl(block), "money")
        roi_text, roi_color = format_cell(calculations.block_roi(block), "percent")
        capital_text_block, _ = format_cell(block.capital, "money_simple")
        rows.append(
            ft.DataRow(
                key=f"block-{block.row_start}",
                cells=[
                    ft.DataCell(ft.Text(block.label, size=12, weight=ft.FontWeight.BOLD)),
                    ft.DataCell(ft.Text(f"{block.row_start}–{block.row_end}", size=12)),
                    ft.DataCell(ft.Text(str(len(block.legs)), size=12)),
                    ft.DataCell(ft.Text(capital_text_block, size=12)),
                    ft.DataCell(ft.Text(net_entry_text, size=12)),
                    ft.DataCell(ft.Text(net_mark_text, size=12)),
                    ft.DataCell(ft.Text(roll_text, size=12, color=roll_color)),
                    ft.DataCell(ft.Text(pl_text, size=12, color=pl_color)),
                    ft.DataCell(ft.Text(roi_text, size=12, color=roi_color)),
                ],
            )
        )

    table = fdt.DataTable2(
        key="summary-blocks",
        columns=[
            _column("Strategy", 120),
            _column("Rows", 100),
            _column("Legs", 80, True),
            _column("Capital", 110, True),
            _column("Net Entry", 110, True),
            _column("Net Mark", 110, True),
            _column("Rolling P/L", 120, True),
            _column("Total P/L", 120, True),
            _column("ROI", 90, True),
        ],
        rows=rows,
        fixed_top_rows=1,
        min_width=980,
        data_row_height=34,
        empty=ft.Text("No blocks yet."),
    )

    warnings: list[str] = []
    # Rows whose K-divisor override is already reported per block below; the
    # importer emits the same fact per row, so those lines are folded away.
    divisor_warned_rows: set[int] = set()
    for block in state.blocks():
        if calculations.net_divisor_overridden(block):
            divisor_warned_rows.add(block.row_end)
            warnings.append(
                f"Block {block.row_start}–{block.row_end}: Net MARK divisor overridden "
                f"({block.net_divisor:g} ≠ qty {block.last_leg().qty:g}) — preserved from the source workbook."
            )
        roi_value = calculations.block_roi(block)
        if roi_value is not None and roi_value < -1.0:
            warnings.append(
                f"Block {block.row_start}–{block.row_end}: ROI {roi_value * 100:.2f}% is below −100% "
                "(not clamped — matches the workbook)."
            )
    placeholders = [leg for leg in state.legs() if leg.quote.is_placeholder]
    if placeholders:
        warnings.append(
            "Placeholder quotes (no live data) on rows: "
            + ", ".join(str(leg.sheet_row) for leg in placeholders)
            + " — displayed marks come from a stale snapshot."
        )
    for warning in state.warnings:
        match = _ROW_DIVISOR_WARNING_RE.match(warning)
        if match and int(match.group(1)) in divisor_warned_rows:
            continue  # already reported as a block-level override above
        if warning in warnings:
            continue
        warnings.append(warning)

    return ft.Column(
        expand=True,
        spacing=12,
        controls=[
            ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Text("Portfolio Summary", size=20, weight=ft.FontWeight.BOLD, key="summary-title"),
                    ft.Button(
                        "Back to Positions",
                        key="back-to-positions",
                        icon=ft.Icons.ARROW_BACK,
                        on_click=lambda e: state.navigate("/"),
                    ),
                ],
            ),
            cards,
            ft.Text("Blocks", size=14, weight=ft.FontWeight.BOLD),
            ft.Container(content=table, height=260),
            ft.Text("Warnings", size=14, weight=ft.FontWeight.BOLD),
            ft.Container(
                content=ft.Column(
                    spacing=4,
                    controls=[ft.Text(text, size=12, color=ft.Colors.ORANGE_900) for text in warnings]
                    or [ft.Text("No warnings.", size=12, color=ft.Colors.GREY_700)],
                )
            ),
        ],
    )
