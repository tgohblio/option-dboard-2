"""Persistence: the app's live JSON store, serialization, and bootstrap order.

The workbook is an import source / export artifact only — the live database is a
versioned JSON document written atomically (docs/spec.md §10).  This module owns
the dict<->model conversion so the rest of the app only ever sees dataclasses.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .models import (
    Leg,
    MonthlyLedger,
    MonthlyMonth,
    MonthlyPnlEntry,
    Portfolio,
    PositionBlock,
    QuoteSnapshot,
    RollLedger,
    RollPair,
    VisibleQuote,
    coerce_float,
)

__all__ = [
    "STORE_VERSION",
    "StoreError",
    "StoreCorruptError",
    "DataSourceError",
    "BootResult",
    "Store",
    "default_store_path",
    "bundled_seed_path",
    "portfolio_to_payload",
    "portfolio_from_payload",
    "ledger_to_payload",
    "ledger_from_payload",
    "bootstrap",
    "load_seed",
]

STORE_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_XLSX = Path.home() / "Downloads" / "OTPTracker.xlsx"


class StoreError(Exception):
    """Base class for persistence errors."""


class StoreCorruptError(StoreError):
    """The store file exists but cannot be parsed as a version-1 document."""


class DataSourceError(StoreError):
    """No usable data source: no store, no workbook, no bundled seed."""


def default_store_path() -> Path:
    """Store path resolution order (docs/spec.md §10.1)."""
    env_path = os.environ.get("OTP_TRACKER_STORE")
    if env_path:
        return Path(env_path).expanduser()
    bundle_data = os.environ.get("FLET_APP_STORAGE_DATA")
    if bundle_data:
        return Path(bundle_data) / "otp_tracker_store.json"
    return PROJECT_ROOT / "data" / "app_store.json"


def bundled_seed_path() -> Path:
    return Path(__file__).with_name("seed_otp.json")


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# dataclass -> dict
# --------------------------------------------------------------------------- #
def _quote_to_payload(quote: QuoteSnapshot) -> dict[str, Any]:
    return {
        "ticker": quote.ticker,
        "info": quote.info,
        "itm": quote.itm,
        "dte": quote.dte,
        "underlying_price": quote.underlying_price,
        "mark_price": quote.mark_price,
        "extrinsic_value": quote.extrinsic_value,
        "price_change": quote.price_change,
        "delta": quote.delta,
        "gamma": quote.gamma,
        "theta": quote.theta,
        "vega": quote.vega,
        "raw": quote.raw,
    }


def _visible_to_payload(visible: VisibleQuote) -> dict[str, Any]:
    return {
        "mark_price": visible.mark_price,
        "underlying_price": visible.underlying_price,
        "price_change": visible.price_change,
        "delta": visible.delta,
        "theta": visible.theta,
        "itm": visible.itm,
        "ev": visible.ev,
        "dte": visible.dte,
    }


def _leg_to_payload(leg: Leg) -> dict[str, Any]:
    return {
        "sheet_row": leg.sheet_row,
        "symbol": leg.symbol,
        "entry_date": _iso(leg.entry_date),
        "qty": leg.qty,
        "entry_price": leg.entry_price,
        "action_note": leg.action_note,
        "quote": _quote_to_payload(leg.quote),
        "visible": _visible_to_payload(leg.visible),
        "has_ev_column": leg.has_ev_column,
        "purple_cells": list(leg.purple_cells),
        "extra_cells": dict(leg.extra_cells),
    }


def _roll_to_payload(roll: RollLedger) -> dict[str, Any]:
    return {
        "pairs": [{"new_px": pair.new_px, "closed_px": pair.closed_px} for pair in roll.pairs],
        "credits": list(roll.credits),
        "literal_zero": roll.literal_zero,
    }


def portfolio_to_payload(portfolio: Portfolio) -> dict[str, Any]:
    return {
        "version": STORE_VERSION,
        "source": None,
        "as_of_date": _iso(portfolio.as_of_date),
        "blocks": [
            {
                "row_start": block.row_start,
                "row_end": block.row_end,
                "strategy": block.strategy,
                "capital": block.capital,
                "net_divisor": block.net_divisor,
                "roll": _roll_to_payload(block.roll),
                "legs": [_leg_to_payload(leg) for leg in block.legs],
            }
            for block in portfolio.blocks
        ],
    }


def ledger_to_payload(ledger: MonthlyLedger) -> dict[str, Any]:
    return {
        "months": [
            {
                "month": _iso(month.month),
                "entries": [
                    {"ticker": entry.ticker, "pnl": entry.pnl, "extra": entry.extra}
                    for entry in month.entries
                ],
            }
            for month in ledger.months
        ]
    }


def make_payload(
    portfolio: Portfolio,
    ledger: MonthlyLedger,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = portfolio_to_payload(portfolio)
    payload["source"] = copy.deepcopy(source) if source else None
    payload["ledger"] = ledger_to_payload(ledger)
    return payload


# --------------------------------------------------------------------------- #
# dict -> dataclass
# --------------------------------------------------------------------------- #
def _quote_from_payload(payload: dict[str, Any] | None) -> QuoteSnapshot:
    payload = payload or {}
    return QuoteSnapshot(
        ticker=str(payload.get("ticker", "None")),
        info=str(payload.get("info", "None")),
        itm=bool(payload.get("itm", False)),
        dte=coerce_float(payload.get("dte")),
        underlying_price=coerce_float(payload.get("underlying_price")),
        mark_price=coerce_float(payload.get("mark_price")),
        extrinsic_value=coerce_float(payload.get("extrinsic_value")),
        price_change=coerce_float(payload.get("price_change")),
        delta=coerce_float(payload.get("delta")),
        gamma=coerce_float(payload.get("gamma")),
        theta=coerce_float(payload.get("theta")),
        vega=coerce_float(payload.get("vega")),
        raw=str(payload.get("raw") or ""),
    )


def _visible_from_payload(payload: dict[str, Any] | None) -> VisibleQuote:
    payload = payload or {}
    return VisibleQuote(
        mark_price=coerce_float(payload.get("mark_price")),
        underlying_price=coerce_float(payload.get("underlying_price")),
        price_change=coerce_float(payload.get("price_change")),
        delta=coerce_float(payload.get("delta")),
        theta=coerce_float(payload.get("theta")),
        itm=bool(payload.get("itm", False)),
        ev=coerce_float(payload.get("ev")),
        dte=coerce_float(payload.get("dte")),
    )


def _leg_from_payload(payload: dict[str, Any]) -> Leg:
    symbol = payload.get("symbol")
    entry_price = payload.get("entry_price")
    return Leg(
        sheet_row=int(payload["sheet_row"]),
        symbol=str(symbol) if symbol not in (None, "") else None,
        entry_date=_date(payload.get("entry_date")),
        qty=coerce_float(payload.get("qty")),
        entry_price=None if entry_price is None else coerce_float(entry_price),
        action_note=payload.get("action_note") or None,
        quote=_quote_from_payload(payload.get("quote")),
        visible=_visible_from_payload(payload.get("visible")),
        has_ev_column=bool(payload.get("has_ev_column", False)),
        purple_cells=[str(col) for col in payload.get("purple_cells", [])],
        extra_cells=dict(payload.get("extra_cells") or {}),
    )


def _roll_from_payload(payload: dict[str, Any] | None) -> RollLedger:
    payload = payload or {}
    pairs = [
        RollPair(new_px=coerce_float(pair.get("new_px")), closed_px=coerce_float(pair.get("closed_px")))
        for pair in payload.get("pairs", [])
    ]
    credits = [coerce_float(credit) for credit in payload.get("credits", [])]
    return RollLedger(pairs=pairs, credits=credits, literal_zero=bool(payload.get("literal_zero", False)))


def portfolio_from_payload(payload: dict[str, Any]) -> Portfolio:
    portfolio = Portfolio(as_of_date=_date(payload.get("as_of_date")))
    for block_payload in payload.get("blocks", []):
        block = PositionBlock(
            row_start=int(block_payload["row_start"]),
            row_end=int(block_payload["row_end"]),
            strategy=block_payload.get("strategy") or None,
            capital=None
            if block_payload.get("capital") is None
            else coerce_float(block_payload.get("capital")),
            net_divisor=None
            if block_payload.get("net_divisor") is None
            else coerce_float(block_payload.get("net_divisor")),
            roll=_roll_from_payload(block_payload.get("roll")),
        )
        block.legs = [_leg_from_payload(leg) for leg in block_payload.get("legs", [])]
        portfolio.blocks.append(block)
    source = payload.get("source") or {}
    portfolio.source_checksum = source.get("sha256")
    return portfolio


def ledger_from_payload(payload: dict[str, Any] | None) -> MonthlyLedger:
    ledger = MonthlyLedger()
    for month_payload in (payload or {}).get("months", []):
        month = _date(month_payload.get("month"))
        if month is None:
            continue
        entries = [
            MonthlyPnlEntry(
                ticker=str(entry.get("ticker", "")),
                pnl=coerce_float(entry.get("pnl")),
                extra=None if entry.get("extra") is None else coerce_float(entry.get("extra")),
            )
            for entry in month_payload.get("entries", [])
        ]
        ledger.months.append(MonthlyMonth(month=date(month.year, month.month, 1), entries=entries))
    ledger.sort()
    return ledger


def validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise StoreCorruptError("Store root must be a JSON object")
    version = payload.get("version")
    if version != STORE_VERSION:
        raise StoreCorruptError(f"Unsupported store version {version!r} (expected {STORE_VERSION})")
    if not isinstance(payload.get("blocks"), list):
        raise StoreCorruptError("Store is missing the 'blocks' list")
    return payload


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
@dataclass
class Store:
    """Atomic JSON store for one portfolio + the monthly ledger."""

    path: Path = field(default_factory=default_store_path)

    def exists(self) -> bool:
        return self.path.is_file()

    def load_payload(self) -> dict[str, Any] | None:
        """Return the payload, ``None`` when absent, StoreCorruptError when bad."""
        if not self.exists():
            return None
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            raise StoreCorruptError(f"Cannot read {self.path}: {exc}") from exc
        return validate_payload(payload)

    def save_payload(self, payload: dict[str, Any]) -> None:
        """Write atomically (temp file in the same directory + ``os.replace``)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(self.path.parent),
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temp_path = Path(handle.name)
        try:
            with handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except OSError:
            temp_path.unlink(missing_ok=True)
            raise

    def backup_corrupt(self) -> Path | None:
        if not self.exists():
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(f"{self.path.name}.bak-{stamp}")
        try:
            os.replace(self.path, backup)
        except OSError:
            return None
        return backup


@dataclass
class BootResult:
    portfolio: Portfolio
    ledger: MonthlyLedger
    store: Store
    source: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    #: where the data came from: "store" | "xlsx" | "seed" (docs/spec.md §10.1)
    origin: str = "store"

    @property
    def payload(self) -> dict[str, Any]:
        return make_payload(self.portfolio, self.ledger, self.source)


def load_seed() -> BootResult:
    """Load the committed bundle seed (never touches the user's workbook)."""
    path = bundled_seed_path()
    if not path.is_file():
        raise DataSourceError(f"Bundled seed missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise DataSourceError(f"Bundled seed is corrupt: {exc}") from exc
    payload = validate_payload(payload)
    return BootResult(
        portfolio=portfolio_from_payload(payload),
        ledger=ledger_from_payload(payload.get("ledger")),
        store=Store(),
        source=payload.get("source"),
        origin="seed",
    )


def bootstrap(store: Store | None = None, xlsx_path: Path | str | None = None) -> BootResult:
    """Resolve data in the documented order: store -> workbook -> seed -> error."""
    store = store or Store()
    warnings: list[str] = []

    try:
        payload = store.load_payload()
    except StoreCorruptError as exc:
        backup = store.backup_corrupt()
        warnings.append(
            f"Store was corrupt ({exc}); "
            + (f"backed up to {backup.name}" if backup else "could not back it up")
        )
        payload = None
    if payload is not None:
        return BootResult(
            portfolio=portfolio_from_payload(payload),
            ledger=ledger_from_payload(payload.get("ledger")),
            store=store,
            source=payload.get("source"),
            warnings=warnings,
            origin="store",
        )

    candidate = Path(xlsx_path).expanduser() if xlsx_path else Path(
        os.environ.get("OTP_TRACKER_XLSX", DEFAULT_XLSX)
    ).expanduser()
    if candidate.is_file():
        from . import excel_io  # local import: keeps store importable without openpyxl work

        try:
            imported = excel_io.import_workbook(candidate)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, never swallowed
            warnings.append(f"Could not import {candidate}: {exc}")
        else:
            warnings.extend(imported.warnings)
            result = BootResult(
                portfolio=imported.portfolio,
                ledger=imported.ledger,
                store=store,
                source=imported.source,
                warnings=warnings,
                origin="xlsx",
            )
            store.save_payload(result.payload)
            return result

    try:
        seed = load_seed()
    except DataSourceError:
        raise DataSourceError(
            "No data source found. Place OTPTracker.xlsx at ~/Downloads/OTPTracker.xlsx, "
            "set OTP_TRACKER_XLSX, or repair the app bundle."
        ) from None
    seed.store = store
    seed.warnings = [*warnings, *seed.warnings]
    return seed
