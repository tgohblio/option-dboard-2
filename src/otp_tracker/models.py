"""Dataclasses mirroring the OTPTracker workbook.

One model per Excel concept: a `Leg` is one sheet row, a `PositionBlock` is a
consecutive run of legs delimited by a non-empty `Strategy` cell (column B) and
carrying the block-level `Capital` (column I) and `Rolling P/L` (column X) on its
last row.  Money is ``float`` — values are small and only used for display plus
single multiplications (docs/spec.md §5).

Serialization (``to_dict``/``from_dict``) lives in :mod:`otp_tracker.store`; this
module only owns the in-memory shape and the quote/roll parsing that is intrinsic
to a model instance.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

__all__ = [
    "QuoteParseError",
    "QuoteSnapshot",
    "VisibleQuote",
    "Leg",
    "RollPair",
    "RollLedger",
    "PositionBlock",
    "Portfolio",
    "MonthlyPnlEntry",
    "MonthlyMonth",
    "MonthlyLedger",
    "coerce_float",
    "QUOTE_JSON_TO_FIELD",
]

#: JSON key -> QuoteSnapshot field.  The workbook's comment blobs use camelCase
#: keys; the parser is deliberately order-independent and tolerant of missing
#: keys (docs/spec.md §7.1).
QUOTE_JSON_TO_FIELD: dict[str, str] = {
    "ticker": "ticker",
    "info": "info",
    "itm": "itm",
    "dte": "dte",
    "underlyingPrice": "underlying_price",
    "markPrice": "mark_price",
    "extrinsicValue": "extrinsic_value",
    "priceChange": "price_change",
    "delta": "delta",
    "gamma": "gamma",
    "theta": "theta",
    "vega": "vega",
}


class QuoteParseError(ValueError):
    """Raised when a pasted quote blob is not a usable JSON object."""


def coerce_float(value: Any, default: float = 0.0) -> float:
    """Coerce Excel/JSON scalars to float.

    ``extrinsicValue`` is a *string* in the real quote blobs (``"1.170"``) and a
    number in the placeholder blobs — both must land in the same field
    (docs/spec.md §3.4).
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("$", "")
        if not text or text.lower() in {"none", "nan", "null"}:
            return default
        try:
            return float(text)
        except ValueError:
            return default
    return default


@dataclass
class QuoteSnapshot:
    """The cell-comment quote blob attached to one leg row (column C comment)."""

    ticker: str = "None"
    info: str = "None"
    itm: bool = False
    dte: float = 0.0
    underlying_price: float = 0.0
    mark_price: float = 0.0
    extrinsic_value: float = 0.0
    price_change: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    raw: str = ""

    @property
    def is_placeholder(self) -> bool:
        """True for the failed-lookup blobs (ticker "None" / all zeros)."""
        if self.ticker.strip() in {"", "None"} and self.info.strip() in {"", "None"}:
            return True
        return (
            self.mark_price == 0.0
            and self.dte == 0.0
            and self.delta == 0.0
            and self.underlying_price == 0.0
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any], raw: str = "") -> "QuoteSnapshot":
        quote = cls(raw=raw)
        for json_key, field_name in QUOTE_JSON_TO_FIELD.items():
            if json_key not in payload:
                continue
            value = payload[json_key]
            if field_name == "ticker" or field_name == "info":
                setattr(quote, field_name, "None" if value is None else str(value))
            elif field_name == "itm":
                setattr(quote, field_name, bool(value))
            else:
                setattr(quote, field_name, coerce_float(value))
        return quote

    @classmethod
    def from_json_text(cls, text: str) -> "QuoteSnapshot":
        stripped = (text or "").strip()
        if not stripped:
            raise QuoteParseError("Empty quote JSON")
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise QuoteParseError(f"Invalid quote JSON: {exc.msg} (line {exc.lineno})") from exc
        if not isinstance(payload, dict):
            raise QuoteParseError("Quote JSON must be a single object (use the batch dialog for arrays)")
        return cls.from_mapping(payload, raw=stripped)

    def to_visible(self) -> "VisibleQuote":
        """Map a real quote onto the per-share sheet columns J,N,O,Q,S,T,V,W."""
        return VisibleQuote(
            mark_price=self.mark_price,
            underlying_price=self.underlying_price,
            price_change=self.price_change,
            delta=self.delta,
            theta=self.theta,
            itm=self.itm,
            ev=self.extrinsic_value,
            dte=self.dte,
        )


@dataclass
class VisibleQuote:
    """Per-share quote values exactly as stored in the sheet cells."""

    mark_price: float = 0.0  # J
    underlying_price: float = 0.0  # N
    price_change: float = 0.0  # O
    delta: float = 0.0  # Q
    theta: float = 0.0  # S
    itm: bool = False  # T
    ev: float = 0.0  # V
    dte: float = 0.0  # W


@dataclass
class Leg:
    """One data row of the ``Open Trades`` sheet."""

    sheet_row: int
    symbol: str | None
    entry_date: date | None
    qty: float
    entry_price: float | None  # None = blank cell (NOT zero) — block-1 edge case
    action_note: str | None = None
    quote: QuoteSnapshot = field(default_factory=QuoteSnapshot)
    visible: VisibleQuote = field(default_factory=VisibleQuote)
    has_ev_column: bool = False
    purple_cells: list[str] = field(default_factory=list)
    extra_cells: dict[str, object] = field(default_factory=dict)

    @property
    def is_short(self) -> bool:
        return self.qty < 0


@dataclass
class RollPair:
    """One roll event: the new leg price and the price the old leg was closed at."""

    new_px: float
    closed_px: float

    @property
    def delta(self) -> float:
        return self.new_px - self.closed_px


def _fmt_number(value: float) -> str:
    """Minimal decimal rendering used to rebuild the Excel X literal."""
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


_ROLL_PAIR_RE = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*\)")
_ROLL_TERM_RE = re.compile(r"([+-])\s*(\d+(?:\.\d+)?)")
_ROLL_SCALE_RE = re.compile(r"\*\s*100\s*$")
# Characters that may legitimately survive pair/credit extraction: whitespace,
# grouping parentheses and the '+' separators that sit between terms.
_ROLL_STRUCTURAL_CHARS = " \t\r\n()+"


@dataclass
class RollLedger:
    """Human-maintained rolling P/L (column X).

    Each pair contributes ``(new - closed)``; trailing unpaired terms are credits.
    The whole sum is multiplied by 100 (per-contract dollars).
    """

    pairs: list[RollPair] = field(default_factory=list)
    credits: list[float] = field(default_factory=list)
    literal_zero: bool = False

    def value(self) -> float:
        if self.literal_zero:
            return 0.0
        return (sum(p.new_px - p.closed_px for p in self.pairs) + sum(self.credits)) * 100.0

    def render(self) -> str:
        """Rebuild the Excel formula literal, e.g. ``=((1.34-1.55)+1.07)*100``."""
        if self.literal_zero:
            return "0"
        terms = "+".join(f"({_fmt_number(p.new_px)}-{_fmt_number(p.closed_px)})" for p in self.pairs)
        for credit in self.credits:
            terms += f"+{_fmt_number(credit)}" if credit >= 0 else _fmt_number(credit)
        return f"=({terms})*100"

    def is_empty(self) -> bool:
        return not self.pairs and not self.credits and not self.literal_zero

    @classmethod
    def from_expression(cls, raw: str) -> "RollLedger":
        """Parse an Excel X literal such as ``=((1.34-1.55)+(2.46-3)+1.07)*100``.

        ``0`` / ``0.0`` become :attr:`literal_zero`; unparseable text raises
        :class:`ValueError` so the dialog can show an inline error.

        Parsing is structural: ``(new-closed)`` groups are consumed wherever they
        appear (the outer wrapper of a rendered literal is just punctuation), the
        remaining signed numbers become credits, and anything left over that is
        not grouping punctuation is rejected rather than silently dropped.
        """
        text = (raw or "").strip()
        if text.startswith("="):
            text = text[1:].strip()
        if not text:
            return cls()
        if re.fullmatch(r"-?0(?:\.0+)?", text):
            return cls(literal_zero=True)

        body = _ROLL_SCALE_RE.sub("", text).strip()

        pairs: list[RollPair] = []
        pair_chunks: list[str] = []
        cursor = 0
        for match in _ROLL_PAIR_RE.finditer(body):
            pair_chunks.append(body[cursor : match.start()])
            pairs.append(RollPair(new_px=float(match.group(1)), closed_px=float(match.group(2))))
            cursor = match.end()
        pair_chunks.append(body[cursor:])
        residue = "".join(pair_chunks)

        credits: list[float] = []
        term_chunks: list[str] = []
        cursor = 0
        for match in _ROLL_TERM_RE.finditer(residue):
            term_chunks.append(residue[cursor : match.start()])
            credits.append(float(f"{match.group(1)}{match.group(2)}"))
            cursor = match.end()
        term_chunks.append(residue[cursor:])

        leftover = "".join(term_chunks).strip()
        # Only the grouping/separator punctuation may remain; digits, letters or
        # stray signs mean the literal is not understood.
        stray = leftover.strip(_ROLL_STRUCTURAL_CHARS)
        if stray:
            raise ValueError(f"Cannot parse roll literal {raw!r}: unexpected text {stray!r}")
        if not pairs and not credits:
            raise ValueError(f"Cannot parse roll literal {raw!r}: no (new-closed) pair or credit found")
        return cls(pairs=pairs, credits=credits)


@dataclass
class PositionBlock:
    """A strategy block: consecutive leg rows sharing strategy/capital/roll."""

    row_start: int
    row_end: int
    strategy: str | None = None
    capital: float | None = None
    net_divisor: float | None = None  # K-formula override (K13 quirk = 1000)
    roll: RollLedger = field(default_factory=RollLedger)
    legs: list[Leg] = field(default_factory=list)

    def last_leg(self) -> "Leg":
        if not self.legs:
            raise ValueError(f"Block at row {self.row_start} has no legs")
        return max(self.legs, key=lambda leg: leg.sheet_row)

    @property
    def label(self) -> str:
        return self.strategy or "(unnamed)"


@dataclass
class Portfolio:
    """The whole ``Open Trades`` sheet (totals are computed, never stored)."""

    as_of_date: date | None = None
    blocks: list[PositionBlock] = field(default_factory=list)
    source_checksum: str | None = None

    def all_legs(self) -> list[Leg]:
        return [leg for block in self.blocks for leg in block.legs]

    def next_sheet_row(self) -> int:
        rows = [leg.sheet_row for leg in self.all_legs()]
        return (max(rows) + 1) if rows else 5

    def find_block(self, row: int) -> PositionBlock | None:
        for block in self.blocks:
            if block.row_start <= row <= block.row_end:
                return block
        return None


@dataclass
class MonthlyPnlEntry:
    """One ticker row of the ``Sheet6`` monthly realized P/L ledger."""

    ticker: str
    pnl: float
    extra: float | None = None  # ragged C-column value (GLD -680) — never summed


@dataclass
class MonthlyMonth:
    month: date  # first of month
    entries: list[MonthlyPnlEntry] = field(default_factory=list)

    @property
    def total(self) -> float:
        """Sum of the P/L column only; ``extra`` is deliberately excluded."""
        return sum(entry.pnl for entry in self.entries)


@dataclass
class MonthlyLedger:
    months: list[MonthlyMonth] = field(default_factory=list)

    def find(self, month: date) -> MonthlyMonth | None:
        for item in self.months:
            if item.month.year == month.year and item.month.month == month.month:
                return item
        return None

    def sort(self) -> None:
        self.months.sort(key=lambda item: item.month)
