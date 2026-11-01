"""Quote snapshot dialog — the app's equivalent of the workbook's C-cell comments.

Shows all twelve quote fields (including ``gamma``/``vega``, which have no output
column), the visible-column mapping, the raw JSON blob, and a paste box that
accepts one object (this leg) or an array (batch, matched by ticker + info).
Failed-lookup placeholders are surfaced as "no quote" and never overwrite the
visible mark columns (docs/spec.md §2.4, §11.4).
"""

from __future__ import annotations

import json

import flet as ft

from ..models import Leg, QuoteSnapshot

__all__ = ["open_quote_dialog", "apply_quote_array", "apply_quote_to_leg"]

_FIELD_LABELS: tuple[tuple[str, str, str], ...] = (
    ("ticker", "Ticker", ""),
    ("info", "Info", ""),
    ("itm", "ITM", "T (YES/NO)"),
    ("dte", "DTE", "W"),
    ("underlying_price", "Underlying price", "N"),
    ("mark_price", "Mark price", "J"),
    ("extrinsic_value", "Extrinsic value", "V"),
    ("price_change", "Price change", "O"),
    ("delta", "Delta", "Q"),
    ("gamma", "Gamma", "comment only"),
    ("theta", "Theta", "S"),
    ("vega", "Vega", "comment only"),
)


def apply_quote_to_leg(leg: Leg, quote: QuoteSnapshot) -> None:
    """Store the blob; real quotes also refresh the visible per-share columns."""
    leg.quote = quote
    if not quote.is_placeholder:
        leg.visible = quote.to_visible()


def apply_quote_array(legs: list[Leg], payload: list[dict]) -> tuple[int, list[str]]:
    """Match pasted quote objects to legs by ticker+info, then ticker alone."""
    applied = 0
    warnings: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            warnings.append("Skipped a non-object array entry")
            continue
        quote = QuoteSnapshot.from_mapping(item, raw=json.dumps(item, separators=(",", ":")))
        exact = [
            leg
            for leg in legs
            if leg.quote.ticker == quote.ticker and leg.quote.info == quote.info
        ]
        if not exact:
            ticker_matches = [leg for leg in legs if leg.quote.ticker == quote.ticker]
            exact = ticker_matches if len(ticker_matches) == 1 else []
        if not exact:
            warnings.append(f"No leg matched {quote.ticker} / {quote.info}")
            continue
        for leg in exact[:1]:
            apply_quote_to_leg(leg, quote)
            applied += 1
    return applied, warnings


def _value_text(quote: QuoteSnapshot, field: str) -> str:
    value = getattr(quote, field)
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _fields_grid(quote: QuoteSnapshot) -> ft.Column:
    rows: list[ft.Control] = []
    for field, label, mapped in _FIELD_LABELS:
        rows.append(
            ft.Row(
                spacing=8,
                controls=[
                    ft.Text(f"{label}", width=130, size=12, color=ft.Colors.GREY_700),
                    ft.Text(_value_text(quote, field), size=12, weight=ft.FontWeight.W_500, expand=True),
                    ft.Text(f"→ {mapped}" if mapped else "", size=11, color=ft.Colors.GREY_500, width=110),
                ],
            )
        )
    return ft.Column(spacing=2, controls=rows)


def open_quote_dialog(page: ft.Page, state, leg: Leg) -> ft.AlertDialog:
    quote = leg.quote
    status = "No quote — placeholder blob (visible marks kept as stored)" if quote.is_placeholder else quote.info

    paste_field = ft.TextField(
        key="quote-paste",
        hint_text='{"ticker":"AMD","info":"AMD Jul 15 2022 85 Call","markPrice":0.51, ...}',
        multiline=True,
        min_lines=3,
        max_lines=8,
        text_size=11,
    )
    error_text = ft.Text("", key="quote-error", size=12, color=ft.Colors.RED_700)

    body = ft.Column(
        tight=True,
        spacing=10,
        controls=[
            ft.Text(status, size=12, color=ft.Colors.RED_700 if quote.is_placeholder else ft.Colors.GREEN_700),
            ft.Text(f"Row {leg.sheet_row} · {leg.symbol or '(blank OPRA)'}", size=12, color=ft.Colors.GREY_700),
            _fields_grid(quote),
            ft.Divider(height=1),
            ft.Text("Raw comment JSON", size=12, weight=ft.FontWeight.BOLD),
            ft.TextField(
                key="quote-raw",
                value=quote.raw or "",
                multiline=True,
                min_lines=3,
                max_lines=6,
                read_only=True,
                text_size=11,
            ),
            ft.Text("Paste new quote JSON (object for this leg, or array to batch-apply)", size=12, weight=ft.FontWeight.BOLD),
            paste_field,
            error_text,
        ],
    )

    dialog = ft.AlertDialog(
        key="quote-dialog",
        modal=True,
        title=ft.Text(f"Quote — {quote.info if not quote.is_placeholder else 'no quote'}"),
        content=ft.Container(width=640, content=body),
        actions=[
            ft.TextButton("Close", key="quote-cancel", on_click=lambda e: _close(dialog, page)),
            ft.FilledButton("Apply", key="quote-apply", on_click=lambda e: _apply()),
        ],
    )

    def _close(dlg, pg):
        dlg.open = False
        pg.update()

    def _apply() -> None:
        raw_text = (paste_field.value or "").strip()
        if not raw_text:
            _close(dialog, page)
            return
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            error_text.value = f"Invalid JSON: {exc.msg} (line {exc.lineno})"
            page.update()
            return

        if isinstance(payload, list):
            applied, warnings = apply_quote_array(state.legs(), payload)
            message = f"Applied {applied} quote(s)"
            if warnings:
                message += " — " + "; ".join(warnings[:3])
        elif isinstance(payload, dict):
            new_quote = QuoteSnapshot.from_mapping(
                payload, raw=json.dumps(payload, separators=(",", ":"))
            )
            apply_quote_to_leg(leg, new_quote)
            message = (
                f"Quote stored for row {leg.sheet_row} (placeholder — marks unchanged)"
                if new_quote.is_placeholder
                else f"Quote refreshed for row {leg.sheet_row}"
            )
        else:
            error_text.value = "Quote JSON must be an object or an array of objects"
            page.update()
            return

        dialog.open = False
        state.touch(message)
        state.refresh()

    page.show_dialog(dialog)
    return dialog
