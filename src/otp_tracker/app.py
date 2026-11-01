"""Shell: navigation rail + swappable content pane.

Flet 1.0.5 **freezes** controls that a ``@ft.component`` returns, so the imperative
"mutate the bound control then ``page.update()``" pattern used by the CRUD views
cannot live inside component functions.  The shell therefore builds the rail once
and swaps the content pane, rebuilding each view from the model on navigation
(docs/spec.md §11.0 describes the Router variant; this is the equivalent imperative
form for the pinned Flet version).
"""

from __future__ import annotations

import flet as ft

from .app_state import get_state
from .views.ledger_view import build_ledger_view
from .views.positions_view import build_positions_view
from .views.summary_view import build_summary_view

__all__ = ["build_app", "ROUTES"]

ROUTES = ("/", "/summary", "/ledger")
_TITLES = ("Positions", "Summary", "Ledger")


def build_app(page: ft.Page) -> ft.Control:
    state = get_state()
    content = ft.Container(expand=True, padding=12)
    rail = ft.NavigationRail(
        key="main-nav",
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=72,
        group_alignment=-0.9,
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.Icons.TABLE_VIEW_OUTLINED, selected_icon=ft.Icons.TABLE_VIEW, label=_TITLES[0]
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.QUERY_STATS_OUTLINED, selected_icon=ft.Icons.QUERY_STATS, label=_TITLES[1]
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.RECEIPT_LONG_OUTLINED, selected_icon=ft.Icons.RECEIPT_LONG, label=_TITLES[2]
            ),
        ],
    )

    def navigate(route: str) -> None:
        index = ROUTES.index(route) if route in ROUTES else 0
        rail.selected_index = index
        content.content = _build_view(ROUTES[index], page)
        page.update()

    def on_nav_change(event) -> None:
        index = event.control.selected_index or 0
        navigate(ROUTES[index])

    rail.on_change = on_nav_change
    state.navigate = navigate
    content.content = _build_view("/", page)

    return ft.SafeArea(
        expand=True,
        content=ft.Row(
            expand=True,
            spacing=0,
            controls=[rail, ft.VerticalDivider(width=1), content],
        ),
    )


def _build_view(route: str, page: ft.Page) -> ft.Control:
    if route == "/summary":
        return build_summary_view(page)
    if route == "/ledger":
        return build_ledger_view(page)
    return build_positions_view(page)
