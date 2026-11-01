"""Option symbology: OPRA display style and full OSI -> one ParsedSymbol.

The workbook stores *OPRA display* symbols with a leading dot and an unpadded
strike (``.AMD220715C85``); the rest of the repo uses full OSI
(``AMD220715C00085000``).  Both are accepted and normalised to the OSI form
(docs/spec.md §8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

__all__ = ["SymbolParseError", "ParsedSymbol", "parse", "is_valid", "describe_safe"]

_SYMBOL_RE = re.compile(
    r"^(?P<root>[A-Z]{1,6})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<right>[CP])(?P<strike>\d{1,8})$"
)


class SymbolParseError(ValueError):
    """Raised for non-blank symbol text that cannot be parsed."""


@dataclass(frozen=True)
class ParsedSymbol:
    raw: str
    root: str
    expiry: date
    right: str  # "CALL" | "PUT"
    strike: float
    osi: str

    @property
    def is_call(self) -> bool:
        return self.right == "CALL"

    def description(self) -> str:
        """Human label, e.g. ``AMD Oct 21 2022 Call 100``."""
        label = "Call" if self.is_call else "Put"
        return f"{self.root} {self.expiry.strftime('%b %d %Y')} {label} {self.strike:g}"


def _strip_noise(raw: str) -> str:
    text = raw.strip().upper()
    if text.startswith("O:"):
        text = text[2:]
    if text.startswith("."):
        text = text[1:]
    return text.strip()


def parse(raw: str | None) -> ParsedSymbol | None:
    """Parse a symbol.

    Blank/``None`` returns ``None`` (the caller renders an empty cell); any other
    unparseable text raises :class:`SymbolParseError` so a dialog can show the
    error inline instead of silently dropping the leg.
    """
    if raw is None:
        return None
    text = _strip_noise(raw)
    if not text:
        return None

    match = _SYMBOL_RE.match(text)
    if not match:
        raise SymbolParseError(
            f"Unrecognised option symbol {raw!r} — expected e.g. .AMD220715C85 or AMD220715C00085000"
        )

    strike_digits = match.group("strike")
    # Full OSI pads the strike to 8 digits in thousandths; display style is dollars.
    if len(strike_digits) == 8:
        strike_milli = int(strike_digits)
        if strike_milli <= 0:
            raise SymbolParseError(f"Strike must be greater than zero in {raw!r}")
        strike = strike_milli / 1000.0
    else:
        if len(strike_digits) > 5:
            raise SymbolParseError(f"Strike has too many digits in {raw!r}")
        strike = float(strike_digits)
        if strike <= 0:
            raise SymbolParseError(f"Strike must be greater than zero in {raw!r}")

    yy = int(match.group("yy"))
    month = int(match.group("mm"))
    day = int(match.group("dd"))
    try:
        expiry = date(2000 + yy, month, day)
    except ValueError as exc:
        raise SymbolParseError(f"Invalid expiry date in {raw!r}") from exc

    root = match.group("root")
    right = "CALL" if match.group("right") == "C" else "PUT"
    osi = f"{root}{expiry:%y%m%d}{'C' if right == 'CALL' else 'P'}{int(round(strike * 1000)):08d}"
    return ParsedSymbol(raw=raw, root=root, expiry=expiry, right=right, strike=strike, osi=osi)


def is_valid(raw: str | None) -> bool:
    """Non-raising validity probe for inline form feedback."""
    try:
        parse(raw)
    except SymbolParseError:
        return False
    return True


def describe_safe(raw: str | None) -> str:
    """Best-effort label for grids/dialogs; falls back to the raw text."""
    try:
        parsed = parse(raw)
    except SymbolParseError:
        return raw or ""
    return parsed.description() if parsed else ""
