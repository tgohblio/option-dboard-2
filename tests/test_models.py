"""Model + quote parsing + roll-ledger contracts (spec §5, §7.1, §5.3)."""

from __future__ import annotations

from datetime import date

import pytest

from otp_tracker.models import (
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
from tests.conftest import EXPECTED_X_LITERALS, PLACEHOLDER_ROWS


# --------------------------------------------------------------------------- #
# coerce_float — mixed JSON/Excel scalar types (edge case #7)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        (1, 1.0),
        (1.5, 1.5),
        ("1.170", 1.17),
        (" 2.46 ", 2.46),
        ("$1,234.50", 1234.5),
        ("-0.118", -0.118),
        ("", 0.0),
        ("   ", 0.0),
        ("None", 0.0),
        ("null", 0.0),
        ("NaN", 0.0),
        (None, 0.0),
        ("not-a-number", 0.0),
        (True, 1.0),
        (False, 0.0),
        ([], 0.0),
    ],
)
def test_coerce_float(raw, expected):
    assert coerce_float(raw) == pytest.approx(expected)


def test_coerce_float_uses_explicit_default():
    assert coerce_float(None, default=-1.0) == -1.0
    assert coerce_float("bad", default=-1.0) == -1.0


# --------------------------------------------------------------------------- #
# QuoteSnapshot parsing — real blobs and hostile input
# --------------------------------------------------------------------------- #
REAL_BLOB = (
    '{"ticker":"DIS","info":"DIS Mar 18 2022 145 Call","itm":false,"dte":11,'
    '"underlyingPrice":136.48000000000002,"markPrice":1.08,"extrinsicValue":"1.080",'
    '"priceChange":-1.17,"delta":0.206,"gamma":0.03,"theta":-0.118,"vega":0.07}'
)


def test_quote_from_json_text_coerces_string_extrinsic_value():
    quote = QuoteSnapshot.from_json_text(REAL_BLOB)
    assert quote.ticker == "DIS"
    assert quote.info == "DIS Mar 18 2022 145 Call"
    assert quote.itm is False
    assert quote.dte == 11.0
    assert quote.mark_price == 1.08
    assert quote.extrinsic_value == 1.08  # was the string "1.080"
    assert quote.underlying_price == pytest.approx(136.48)
    assert quote.delta == 0.206
    assert quote.theta == -0.118
    assert quote.vega == 0.07
    assert quote.raw == REAL_BLOB
    assert quote.is_placeholder is False


def test_quote_from_mapping_is_key_order_independent_and_tolerates_missing_keys():
    quote = QuoteSnapshot.from_mapping({"markPrice": 2.5, "ticker": "AMD"})
    assert quote.mark_price == 2.5
    assert quote.ticker == "AMD"
    assert quote.info == "None"
    assert quote.dte == 0.0


def test_quote_to_visible_maps_every_column():
    quote = QuoteSnapshot.from_json_text(REAL_BLOB)
    visible = quote.to_visible()
    assert visible == VisibleQuote(
        mark_price=1.08,
        underlying_price=pytest.approx(136.48),
        price_change=-1.17,
        delta=0.206,
        theta=-0.118,
        itm=False,
        ev=1.08,
        dte=11.0,
    )


@pytest.mark.parametrize(
    "text",
    ["", "   ", "{", '{"ticker":', "[]", "[1,2]", "42", '"text"', "null"],
)
def test_invalid_quote_json_raises_typed_error(text):
    with pytest.raises(ValueError):
        QuoteSnapshot.from_json_text(text)


def test_quote_parse_error_is_a_value_error():
    from otp_tracker.models import QuoteParseError

    assert issubclass(QuoteParseError, ValueError)
    with pytest.raises(QuoteParseError):
        QuoteSnapshot.from_json_text("{")


def test_placeholder_detection_on_real_seed_rows(portfolio):
    by_row = {leg.sheet_row: leg for leg in portfolio.all_legs()}
    for row, leg in by_row.items():
        assert leg.quote.is_placeholder is (row in PLACEHOLDER_ROWS), f"row {row}"
        if row in PLACEHOLDER_ROWS:
            assert leg.quote.ticker == "None"


def test_all_zero_blob_is_placeholder():
    assert QuoteSnapshot().is_placeholder is True
    assert QuoteSnapshot(ticker="None", info="None").is_placeholder is True


def test_half_real_blob_is_not_placeholder():
    quote = QuoteSnapshot(ticker="AMD", info="AMD Jul 15 2022 85 Call", mark_price=0.51)
    assert quote.is_placeholder is False


# --------------------------------------------------------------------------- #
# Leg / Portfolio helpers
# --------------------------------------------------------------------------- #
def test_leg_is_short():
    assert Leg(sheet_row=5, symbol=None, entry_date=None, qty=-1, entry_price=None).is_short is True
    assert Leg(sheet_row=5, symbol=None, entry_date=None, qty=1, entry_price=None).is_short is False
    assert Leg(sheet_row=5, symbol=None, entry_date=None, qty=0, entry_price=None).is_short is False


def test_portfolio_helpers(portfolio):
    assert len(portfolio.all_legs()) == 9
    assert portfolio.next_sheet_row() == 14
    assert portfolio.find_block(5) is portfolio.blocks[0]
    assert portfolio.find_block(6) is portfolio.blocks[0]
    assert portfolio.find_block(7) is portfolio.blocks[1]
    assert portfolio.find_block(13) is portfolio.blocks[3]
    assert portfolio.find_block(14) is None
    assert portfolio.find_block(4) is None


def test_portfolio_next_sheet_row_starts_at_five_when_empty():
    assert Portfolio().next_sheet_row() == 5


def test_position_block_label_and_last_leg():
    block = PositionBlock(row_start=5, row_end=6, legs=[
        Leg(sheet_row=6, symbol=None, entry_date=None, qty=1, entry_price=None),
        Leg(sheet_row=5, symbol=None, entry_date=None, qty=1, entry_price=None),
    ])
    assert block.label == "(unnamed)"
    assert block.last_leg().sheet_row == 6
    with pytest.raises(ValueError):
        PositionBlock(row_start=5, row_end=5).last_leg()


# --------------------------------------------------------------------------- #
# RollLedger — value, literal rendering, parsing, round trips
# --------------------------------------------------------------------------- #
def test_real_roll_ledgers_render_the_exact_workbook_literals(portfolio):
    by_last = {block.row_end: block for block in portfolio.blocks}
    for last_row, literal in EXPECTED_X_LITERALS.items():
        assert by_last[last_row].roll.render() == literal, f"X{last_row}"


def test_real_roll_ledger_values(portfolio):
    values = {block.row_end: block.roll.value() for block in portfolio.blocks}
    assert values[6] == pytest.approx(-650.0)
    assert values[8] == pytest.approx(511.0)
    assert values[10] == pytest.approx(188.0)
    assert values[13] == pytest.approx(0.0)


def test_real_roll_ledgers_parse_render_round_trip(portfolio):
    for block in portfolio.blocks:
        if block.roll.literal_zero:
            assert RollLedger.from_expression(block.roll.render()).literal_zero is True
            continue
        parsed = RollLedger.from_expression(block.roll.render())
        assert parsed.value() == pytest.approx(block.roll.value())
        assert [(p.new_px, p.closed_px) for p in parsed.pairs] == [
            (p.new_px, p.closed_px) for p in block.roll.pairs
        ]
        assert parsed.credits == block.roll.credits


@pytest.mark.parametrize("text", ["0", "=0", "0.0", "=0.0", " 0 "])
def test_literal_zero_variants(text):
    ledger = RollLedger.from_expression(text)
    assert ledger.literal_zero is True
    assert ledger.value() == 0.0
    assert ledger.render() == "0"


def test_parenthesised_pair_with_credit():
    ledger = RollLedger.from_expression("=((1.34-1.55)+1.07)*100")
    assert [(p.new_px, p.closed_px) for p in ledger.pairs] == [(1.34, 1.55)]
    assert ledger.credits == [1.07]
    assert ledger.value() == pytest.approx(86.0)


def test_negative_credit_term():
    ledger = RollLedger.from_expression("=((2-1)-0.5)*100")
    assert ledger.value() == pytest.approx(50.0)
    assert ledger.render() == "=((2-1)-0.5)*100"


def test_empty_expression_is_an_empty_ledger():
    for text in ("", "   ", "="):
        ledger = RollLedger.from_expression(text)
        assert ledger.is_empty()
        assert ledger.value() == 0.0


def test_literal_zero_is_not_empty():
    assert RollLedger(literal_zero=True).is_empty() is False


def test_pair_only_literal_round_trip():
    ledger = RollLedger(pairs=[RollPair(2.0, 1.5)])
    parsed = RollLedger.from_expression(ledger.render())
    assert parsed.value() == pytest.approx(50.0)
    assert [(p.new_px, p.closed_px) for p in parsed.pairs] == [(2.0, 1.5)]


def test_unparseable_literal_raises():
    with pytest.raises(ValueError):
        RollLedger.from_expression("=foo*100")


def test_render_uses_minimal_decimals():
    ledger = RollLedger(pairs=[RollPair(2.0, 1.5), RollPair(3.25, 3.0)], credits=[1.0])
    assert ledger.render() == "=((2-1.5)+(3.25-3)+1)*100"


# --------------------------------------------------------------------------- #
# Monthly ledger
# --------------------------------------------------------------------------- #
def test_month_total_excludes_extra(ledger):
    feb = ledger.months[0]
    assert feb.total == pytest.approx(-354 - 295 - 737 - 1090 - 1000)
    assert feb.total == pytest.approx(-3476.0)  # GLD's -680 extra is NOT summed
    assert feb.entries[1].extra == -680.0


def test_month_find_and_sort():
    later = MonthlyMonth(month=date(2022, 3, 1), entries=[MonthlyPnlEntry("XOP", -265.0)])
    earlier = MonthlyMonth(month=date(2022, 2, 1), entries=[])
    ledger = MonthlyLedger(months=[later, earlier])
    assert ledger.find(date(2022, 3, 15)).month == date(2022, 3, 1)
    assert ledger.find(date(2022, 4, 1)) is None
    ledger.sort()
    assert [month.month for month in ledger.months] == [date(2022, 2, 1), date(2022, 3, 1)]
