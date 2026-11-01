"""Pure calculation engine — no Flet imports, fully unit-testable.

Every function here corresponds to one Excel formula class (docs/spec.md §2.3).
The app never evaluates Excel formulas; it recomputes them from the model on
every mutation.  Division guards return ``None`` (rendered ``—``) instead of
raising, so a zero-qty edge case cannot crash the UI.
"""

from __future__ import annotations

from .models import Leg, PositionBlock, Portfolio

__all__ = [
    "NAMED_RANGES",
    "leg_entry_value",
    "leg_mark_value",
    "leg_pl_open",
    "leg_total_delta",
    "leg_total_theta",
    "leg_total_ev",
    "block_net_entry",
    "block_net_mark",
    "block_rolling_pl",
    "block_total_pl",
    "block_roi",
    "portfolio_total_delta",
    "portfolio_total_theta",
    "portfolio_open_pl",
    "portfolio_total_pl",
    "portfolio_capital",
    "render_values",
    "net_divisor_overridden",
]

#: The 13 workbook defined names -> column keys (docs/spec.md §5.5).
#: Kept as documentation of the source formulas that the functions below mirror.
NAMED_RANGES: dict[str, str] = {
    "EntryValue": "H",
    "EntryPrice": "F",
    "Theta": "S",
    "MarkValue": "L",
    "ExtrinsicValue": "V",
    "RollingProfitLoss": "X",
    "Qty": "E",
    "OPRA": "C",
    "MarkPrice": "J",
    "ProfitLossOpen": "M",
    "Delta": "Q",
    "Capital": "I",
    "TotalProfitLoss": "Y",
}


def leg_entry_value(leg: Leg) -> float:
    """Column H — ``=EntryPrice*Qty`` (blank entry price counts as 0)."""
    # `+ 0.0` normalises the -0.0 that 0.0 * negative gives back to 0.0
    return (leg.entry_price or 0.0) * leg.qty + 0.0


def leg_mark_value(leg: Leg) -> float:
    """Column L — ``=MarkPrice*Qty``."""
    return leg.visible.mark_price * leg.qty


def leg_pl_open(leg: Leg) -> float:
    """Column M — ``=MarkValue-EntryValue``."""
    return leg_mark_value(leg) - leg_entry_value(leg)


def leg_total_delta(leg: Leg) -> float:
    """Column P — ``=Delta*Qty``."""
    return leg.visible.delta * leg.qty


def leg_total_theta(leg: Leg) -> float:
    """Column R — ``=Theta*Qty``."""
    return leg.visible.theta * leg.qty


def leg_total_ev(leg: Leg) -> float:
    """Column U — ``=ExtrinsicValue*Qty`` (only rendered when has_ev_column)."""
    return leg.visible.ev * leg.qty


def block_net_entry(block: PositionBlock) -> float | None:
    """Column G — ``=SUM(H<first>:H<last>)/E<last>``."""
    if not block.legs:
        return None
    divisor = block.last_leg().qty
    if divisor == 0:
        return None
    return sum(leg_entry_value(leg) for leg in block.legs) / divisor


def block_net_mark(block: PositionBlock) -> float | None:
    """Column K — ``=SUM(L<first>:L<last>)/divisor``.

    The divisor is the block's ``net_divisor`` override when set (the workbook's
    K13 uses a literal ``1000`` instead of ``E13``), otherwise the last leg qty.
    """
    if not block.legs:
        return None
    divisor = block.net_divisor if block.net_divisor is not None else block.last_leg().qty
    if divisor == 0:
        return None
    return sum(leg_mark_value(leg) for leg in block.legs) / divisor


def block_rolling_pl(block: PositionBlock) -> float:
    """Column X — the human-maintained roll ledger value."""
    return block.roll.value()


def block_total_pl(block: PositionBlock) -> float:
    """Column Y — ``=SUM(M<first>:M<last>)+X<last>``."""
    return sum(leg_pl_open(leg) for leg in block.legs) + block_rolling_pl(block)


def block_roi(block: PositionBlock) -> float | None:
    """Column Z — ``=TotalProfitLoss/Capital`` (never clamped; may be < -100%)."""
    if block.capital is None or block.capital == 0:
        return None
    return block_total_pl(block) / block.capital


def portfolio_total_delta(blocks: list[PositionBlock]) -> float:
    """Row 40 P — ``=SUM(P5:P39)`` over the actual legs."""
    return sum(leg_total_delta(leg) for block in blocks for leg in block.legs)


def portfolio_total_theta(blocks: list[PositionBlock]) -> float:
    """Row 40 R — ``=SUM(R5:R39)`` over the actual legs."""
    return sum(leg_total_theta(leg) for block in blocks for leg in block.legs)


def portfolio_open_pl(blocks: list[PositionBlock]) -> float:
    return sum(leg_pl_open(leg) for block in blocks for leg in block.legs)


def portfolio_total_pl(blocks: list[PositionBlock]) -> float:
    return sum(block_total_pl(block) for block in blocks)


def portfolio_capital(blocks: list[PositionBlock]) -> float:
    return sum(block.capital for block in blocks if block.capital is not None)


def net_divisor_overridden(block: PositionBlock) -> bool:
    """True when K uses a non-default divisor (K13 quirk surfaced as a warning)."""
    if block.net_divisor is None or not block.legs:
        return False
    return block.net_divisor != block.last_leg().qty


def render_values(portfolio: Portfolio) -> dict[str, float | None]:
    """Flat ``{"H5": ...}`` map of every computed cell — used by verification."""
    values: dict[str, float | None] = {}
    for block in portfolio.blocks:
        for leg in block.legs:
            values[f"H{leg.sheet_row}"] = leg_entry_value(leg)
            values[f"L{leg.sheet_row}"] = leg_mark_value(leg)
            values[f"M{leg.sheet_row}"] = leg_pl_open(leg)
            values[f"P{leg.sheet_row}"] = leg_total_delta(leg)
            values[f"R{leg.sheet_row}"] = leg_total_theta(leg)
            if leg.has_ev_column:
                values[f"U{leg.sheet_row}"] = leg_total_ev(leg)
        last = block.row_end
        values[f"G{last}"] = block_net_entry(block)
        values[f"K{last}"] = block_net_mark(block)
        values[f"X{last}"] = block_rolling_pl(block)
        values[f"Y{last}"] = block_total_pl(block)
        values[f"Z{last}"] = block_roi(block)
    values["P40"] = portfolio_total_delta(portfolio.blocks)
    values["R40"] = portfolio_total_theta(portfolio.blocks)
    return values
