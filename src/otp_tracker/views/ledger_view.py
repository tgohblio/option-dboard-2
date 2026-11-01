"""Ledger view — the ``Sheet6`` monthly realized P/L ledger (docs/spec.md §11.3)."""

from __future__ import annotations

from datetime import date, datetime

import flet as ft
import flet_datatable2 as fdt

from ..app_state import get_state
from ..formatting import format_cell
from ..models import MonthlyMonth, MonthlyPnlEntry


def _month_label(month: date) -> str:
    return month.strftime("%b %Y")


def _month_key(month: date) -> str:
    return f"month-{month:%Y-%m}"


def _parse_month(text: str) -> date:
    cleaned = (text or "").strip()
    for fmt in ("%Y-%m", "%m/%Y", "%Y-%m-%d", "%b %Y"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return date(parsed.year, parsed.month, 1)
        except ValueError:
            continue
    raise ValueError(f"Month must look like YYYY-MM (got {text!r})")


def build_ledger_view(page: ft.Page):
    state = get_state()
    ledger = state.ledger

    table = fdt.DataTable2(
        key="ledger-table",
        columns=[
            fdt.DataColumn2(label=ft.Text("Month", size=12, weight=ft.FontWeight.BOLD), fixed_width=130),
            fdt.DataColumn2(label=ft.Text("Ticker", size=12, weight=ft.FontWeight.BOLD), fixed_width=110),
            fdt.DataColumn2(
                label=ft.Text("Realized P/L", size=12, weight=ft.FontWeight.BOLD), fixed_width=130, numeric=True
            ),
            fdt.DataColumn2(
                label=ft.Text("Extra (unlabeled)", size=12, weight=ft.FontWeight.BOLD), fixed_width=140, numeric=True
            ),
            fdt.DataColumn2(label=ft.Text("", size=12), fixed_width=60),
        ],
        rows=[],
        fixed_top_rows=1,
        min_width=600,
        data_row_height=34,
        empty=ft.Text("No months yet — use Add Month."),
    )
    totals_text = ft.Text("", key="ledger-totals", size=12, color=ft.Colors.GREY_800)
    status_text = ft.Text(state.status, key="ledger-status", size=12, color=ft.Colors.GREY_800)

    def rebuild() -> None:
        rows: list[ft.DataRow] = []
        for month in ledger.months:
            rows.append(
                ft.DataRow(
                    key=_month_key(month.month),
                    color="#FFF3F6FA",
                    cells=[
                        ft.DataCell(
                            ft.Text(_month_label(month.month), size=12, weight=ft.FontWeight.BOLD)
                        ),
                        ft.DataCell(ft.Text("", size=12)),
                        ft.DataCell(
                            ft.Text(
                                format_cell(month.total, "money")[0],
                                size=12,
                                weight=ft.FontWeight.BOLD,
                                color=format_cell(month.total, "money")[1],
                            )
                        ),
                        ft.DataCell(ft.Text("", size=12)),
                        ft.DataCell(ft.Text("", size=12)),
                    ],
                )
            )
            for entry in month.entries:
                pnl_text, pnl_color = format_cell(entry.pnl, "money")
                extra_text = "" if entry.extra is None else f"{entry.extra:,.2f}"
                rows.append(
                    ft.DataRow(
                        key=f"entry-{entry.ticker}",
                        cells=[
                            ft.DataCell(ft.Text("", size=12)),
                            ft.DataCell(ft.Text(entry.ticker, size=12)),
                            ft.DataCell(ft.Text(pnl_text, size=12, color=pnl_color)),
                            ft.DataCell(ft.Text(extra_text, size=12, color=ft.Colors.GREY_700)),
                            ft.DataCell(
                                ft.Container(
                                    content=ft.Icon(ft.Icons.DELETE_OUTLINE, size=14, color=ft.Colors.RED_400),
                                    tooltip=f"Delete {entry.ticker} ({_month_label(month.month)})",
                                    on_click=lambda e, month=month, entry=entry: delete_entry(month, entry),
                                    padding=0,
                                )
                            ),
                        ],
                    )
                )
        table.rows = rows
        totals = ", ".join(
            f"{_month_label(month.month)}: {format_cell(month.total, 'money')[0]}" for month in ledger.months
        )
        totals_text.value = f"Month totals (Extra excluded): {totals}" if totals else "No entries."
        page.update()

    def delete_entry(month: MonthlyMonth, entry: MonthlyPnlEntry) -> None:
        month.entries = [item for item in month.entries if item is not entry]
        state.touch(f"Deleted {entry.ticker} from {_month_label(month.month)}")
        state.refresh()
        rebuild()

    def add_month_dialog(e) -> None:
        field = ft.TextField(key="month-field", label="Month", hint_text="2022-04", width=200)
        error = ft.Text("", size=12, color=ft.Colors.RED_700)
        dialog = ft.AlertDialog(
            key="add-month-dialog",
            modal=True,
            title=ft.Text("Add month"),
            content=ft.Column(tight=True, spacing=8, controls=[field, error]),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: _close(dialog)),
                ft.FilledButton("Add", key="save-month", on_click=lambda e: _save()),
            ],
        )

        def _close(dlg):
            dlg.open = False
            page.update()

        def _save() -> None:
            try:
                month_date = _parse_month(field.value or "")
            except ValueError as exc:
                error.value = str(exc)
                page.update()
                return
            if ledger.find(month_date) is not None:
                error.value = f"{_month_label(month_date)} already exists"
                page.update()
                return
            ledger.months.append(MonthlyMonth(month=month_date))
            ledger.sort()
            dialog.open = False
            state.touch(f"Added {_month_label(month_date)}")
            rebuild()

        page.show_dialog(dialog)

    def add_entry_dialog(e) -> None:
        if not ledger.months:
            state.set_status("Add a month first.")
            page.update()
            return
        month_field = ft.Dropdown(
            key="entry-month",
            label="Month",
            width=180,
            value=f"{ledger.months[-1].month:%Y-%m}",
            options=[
                ft.DropdownOption(key=f"{month.month:%Y-%m}", text=_month_label(month.month))
                for month in ledger.months
            ],
        )
        ticker_field = ft.TextField(key="entry-ticker", label="Ticker", width=140)
        pnl_field = ft.TextField(key="entry-pnl", label="Realized P/L", width=140)
        extra_field = ft.TextField(key="entry-extra", label="Extra (optional)", width=140)
        error = ft.Text("", size=12, color=ft.Colors.RED_700)
        dialog = ft.AlertDialog(
            key="add-entry-dialog",
            modal=True,
            title=ft.Text("Add realized P/L entry"),
            content=ft.Column(
                tight=True,
                spacing=8,
                controls=[month_field, ft.Row([ticker_field, pnl_field, extra_field], spacing=8), error],
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: _close(dialog)),
                ft.FilledButton("Add", key="save-entry", on_click=lambda e: _save()),
            ],
        )

        def _close(dlg):
            dlg.open = False
            page.update()

        def _save() -> None:
            try:
                month_key = str(month_field.value or "")
                year, _, month_part = month_key.partition("-")
                target = ledger.find(date(int(year), int(month_part), 1))
                if target is None:
                    raise ValueError("Unknown month")
                ticker = (ticker_field.value or "").strip().upper()
                if not ticker:
                    raise ValueError("Ticker is required")
                pnl = float((pnl_field.value or "").replace(",", ""))
                extra_text = (extra_field.value or "").strip()
                extra = float(extra_text.replace(",", "")) if extra_text else None
            except ValueError as exc:
                error.value = str(exc)
                page.update()
                return
            target.entries.append(MonthlyPnlEntry(ticker=ticker, pnl=pnl, extra=extra))
            dialog.open = False
            state.touch(f"Added {ticker} to {_month_label(target.month)}")
            rebuild()

        page.show_dialog(dialog)

    rebuild()

    return ft.Column(
        expand=True,
        spacing=10,
        controls=[
            ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Text("Monthly Realized P/L", size=20, weight=ft.FontWeight.BOLD, key="ledger-title"),
                    ft.Button(
                        "Back to Positions",
                        key="ledger-back",
                        icon=ft.Icons.ARROW_BACK,
                        on_click=lambda e: state.navigate("/"),
                    ),
                ],
            ),
            ft.Row(
                spacing=8,
                controls=[
                    ft.FilledButton("Add Month", key="add-month", icon=ft.Icons.CALENDAR_MONTH, on_click=add_month_dialog),
                    ft.Button("Add Entry", key="add-entry", icon=ft.Icons.ADD, on_click=add_entry_dialog),
                ],
            ),
            ft.Container(content=table, expand=True),
            status_text,
            totals_text,
        ],
    )
