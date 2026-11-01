"""Entry point: bootstrap the store, wire the close guard, render the app.

Launch with ``flet run`` (desktop window) or ``flet run --web -p 8550`` from the
project root, or ``python src/main.py``.
"""

from __future__ import annotations

import sys

import flet as ft

from otp_tracker.app import build_app
from otp_tracker.app_state import init_state
from otp_tracker.store import DataSourceError, bootstrap


def _show_fatal(page: ft.Page, message: str) -> None:
    page.add(
        ft.SafeArea(
            expand=True,
            content=ft.Column(
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
                controls=[
                    ft.Icon(ft.Icons.ERROR_OUTLINE, size=48, color=ft.Colors.RED_700),
                    ft.Text("No data source found", size=20, weight=ft.FontWeight.BOLD),
                    ft.Text(message, size=13, text_align=ft.TextAlign.CENTER),
                    ft.Text(
                        "Set OTP_TRACKER_XLSX to a workbook path, place OTPTracker.xlsx in "
                        "~/Downloads, or set OTP_TRACKER_STORE to an existing store file.",
                        size=12,
                        color=ft.Colors.GREY_700,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
            ),
        )
    )
    print(f"OTP Tracker: {message}", file=sys.stderr)


def main(page: ft.Page) -> None:
    page.title = "OTP Tracker"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 0
    page.window.min_width = 1100
    page.window.min_height = 700

    try:
        boot = bootstrap()
    except DataSourceError as exc:
        _show_fatal(page, str(exc))
        return

    state = init_state(boot, page)

    if not page.web:
        # Built apps terminate without flushing: flush before the window closes.
        try:
            page.window.prevent_close = True

            def on_window_event(event) -> None:
                if "close" in str(getattr(event, "type", "")).lower():
                    state.save_now()
                    page.window.destroy()

            page.window.on_event = on_window_event
        except Exception:  # noqa: BLE001 - close guard must never block startup
            pass

    # ``state.status`` already composes the data-source notice plus any bootstrap
    # warnings (see app_state._boot_status); do not overwrite it here or a
    # successful workbook import never reports what it loaded.
    page.add(build_app(page))


if __name__ == "__main__":
    ft.run(main)
