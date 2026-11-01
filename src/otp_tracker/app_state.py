"""Application state: the single Portfolio + MonthlyLedger, live control refs, autosave.

Views are rebuilt by the router when the route changes; within a route they
mutate the model and then call :meth:`AppState.refresh`, which **rebinds** the
grid collections (Flet pitfall A) and flushes the UI.  Persistence is debounced
500 ms so every mutation survives an abrupt close (docs/spec.md §11.6, §12).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import flet as ft

from . import calculations, store
from .models import Leg, MonthlyLedger, Portfolio, PositionBlock
from .store import BootResult, DataSourceError, Store

__all__ = ["AppState", "init_state", "get_state", "DataSourceError"]

_AUTOSAVE_DELAY = 0.5


@dataclass
class AppState:
    portfolio: Portfolio
    ledger: MonthlyLedger
    store: Store
    source: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)

    show_market: bool = True
    selected_leg: Leg | None = None
    status: str = "Ready"
    changed_since_save: bool = False

    page: ft.Page | None = None
    navigate: Any = None  # callable(route) injected by the shell

    # live control bindings, refreshed by each view mount
    grid: Any = None
    status_text: Any = None
    totals: dict[str, Any] = field(default_factory=dict)

    _autosave_pending: bool = False
    _autosave_running: bool = False
    _revision: int = 0

    # ------------------------------------------------------------------ data
    def payload(self) -> dict[str, Any]:
        return store.make_payload(self.portfolio, self.ledger, self.source)

    def blocks(self) -> list[PositionBlock]:
        return self.portfolio.blocks

    def legs(self) -> list[Leg]:
        return self.portfolio.all_legs()

    def block_of(self, leg: Leg) -> PositionBlock | None:
        return self.portfolio.find_block(leg.sheet_row)

    def total_delta(self) -> float:
        return calculations.portfolio_total_delta(self.portfolio.blocks)

    def total_theta(self) -> float:
        return calculations.portfolio_total_theta(self.portfolio.blocks)

    def open_pl(self) -> float:
        return calculations.portfolio_open_pl(self.portfolio.blocks)

    def total_pl(self) -> float:
        return calculations.portfolio_total_pl(self.portfolio.blocks)

    def total_capital(self) -> float:
        return calculations.portfolio_capital(self.portfolio.blocks)

    # ------------------------------------------------------------- mutations
    def next_row(self) -> int:
        return self.portfolio.next_sheet_row()

    def append_leg(self, leg: Leg, block: PositionBlock | None = None) -> PositionBlock:
        """Append ``leg`` to ``block`` (or the last block, creating one if needed)."""
        target = block or (self.portfolio.blocks[-1] if self.portfolio.blocks else None)
        if target is None:
            target = PositionBlock(row_start=leg.sheet_row, row_end=leg.sheet_row)
            self.portfolio.blocks.append(target)
        target.legs.append(leg)
        target.row_end = max(target.row_end, leg.sheet_row)
        return target

    def remove_leg(self, leg: Leg) -> None:
        block = self.block_of(leg)
        if block is None:
            return
        block.legs = [item for item in block.legs if item.sheet_row != leg.sheet_row]
        if not block.legs:
            self.portfolio.blocks = [item for item in self.portfolio.blocks if item is not block]
        else:
            block.row_end = max(item.sheet_row for item in block.legs)
        if self.selected_leg is leg:
            self.selected_leg = None

    def new_leg(self, **kwargs: Any) -> Leg:
        row = self.next_row()
        leg = Leg(sheet_row=row, symbol=None, entry_date=None, qty=0.0, entry_price=None)
        for key, value in kwargs.items():
            setattr(leg, key, value)
        return leg

    # ------------------------------------------------------------ UI plumbing
    def bind(self, grid: Any, status_text: Any, totals: dict[str, Any]) -> None:
        self.grid = grid
        self.status_text = status_text
        self.totals = totals

    def set_status(self, message: str) -> None:
        self.status = message
        if self.status_text is not None:
            self.status_text.value = message

    def refresh(self, message: str | None = None) -> None:
        """Rebind the grid + totals from the model and flush."""
        if message is not None:
            self.status = message
        if self.status_text is not None:
            self.status_text.value = self.status
        if self.grid is not None:
            from .components.position_table import build_columns, build_rows

            self.grid.columns = build_columns(self.show_market)
            self.grid.rows = build_rows(self.portfolio, self.show_market, callbacks=None)
        self._refresh_totals()
        if self.page is not None:
            self.page.update()

    def _refresh_totals(self) -> None:
        from .formatting import format_cell

        updates = {
            "delta": (self.total_delta(), "money"),
            "theta": (self.total_theta(), "money"),
            "pl": (self.total_pl(), "money"),
            "capital": (self.total_capital(), "money_simple"),
        }
        for key, (value, fmt) in updates.items():
            control = self.totals.get(key)
            if control is None:
                continue
            text, color = format_cell(value, fmt)
            control.value = text
            control.color = color

    # ------------------------------------------------------------- autosave
    def touch(self, message: str | None = None) -> None:
        """Mark the state dirty and schedule a debounced save."""
        self.changed_since_save = True
        self._revision += 1
        if message is not None:
            self.set_status(message)
        elif self.status_text is not None:
            self.status_text.value = self.status
        if self.page is not None:
            self.page.run_task(self._debounced_save)

    async def _debounced_save(self) -> None:
        self._autosave_pending = True
        if self._autosave_running:
            return
        self._autosave_running = True
        try:
            while self._autosave_pending:
                self._autosave_pending = False
                await asyncio.sleep(_AUTOSAVE_DELAY)
                if self.changed_since_save:
                    await self.save_async(silent=True)
        finally:
            self._autosave_running = False

    async def save_async(self, silent: bool = False) -> bool:
        revision = self._revision
        try:
            await asyncio.to_thread(self.store.save_payload, self.payload())
        except OSError as exc:
            self.set_status(f"Save failed: {exc}")
            if self.page is not None:
                self.page.update()
            return False
        self.changed_since_save = False
        if not silent or revision != self._revision:
            self.set_status(f"Saved to {self.store.path.name}")
        if self.page is not None:
            self.page.update()
        return True

    def save_now(self) -> bool:
        """Blocking save for shutdown handlers (called off the event loop)."""
        try:
            self.store.save_payload(self.payload())
        except OSError:
            return False
        self.changed_since_save = False
        return True


_STATE: AppState | None = None


def _boot_status(boot: BootResult) -> str:
    """One-line data-source notice for the status strip (docs/spec.md §10.1)."""
    legs = len(boot.portfolio.all_legs())
    blocks = len(boot.portfolio.blocks)
    file_label = (boot.source or {}).get("file") or "workbook"
    if boot.origin == "xlsx":
        status = f"Imported {file_label} — {legs} legs / {blocks} blocks"
    elif boot.origin == "seed":
        status = (
            f"No workbook at {os.environ.get('OTP_TRACKER_XLSX', str(store.DEFAULT_XLSX))} — "
            f"bundled sample data loaded ({legs} legs / {blocks} blocks). "
            "Use Import .xlsx… to load your own copy."
        )
    else:
        status = f"Loaded store {boot.store.path.name} — {legs} legs / {blocks} blocks"
    if boot.warnings:
        status += " · " + " · ".join(boot.warnings[:2])
    return status


def init_state(boot: BootResult, page: ft.Page | None = None) -> AppState:
    global _STATE
    _STATE = AppState(
        portfolio=boot.portfolio,
        ledger=boot.ledger,
        store=boot.store,
        source=boot.source,
        warnings=list(boot.warnings),
        page=page,
        status=_boot_status(boot),
    )
    return _STATE


def get_state() -> AppState:
    if _STATE is None:
        raise RuntimeError("AppState not initialised — call init_state() from main()")
    return _STATE


def default_store_path() -> Path:
    return store.default_store_path()
