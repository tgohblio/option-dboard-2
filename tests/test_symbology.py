"""OPRA display / OSI symbology contract (spec §8)."""

from __future__ import annotations

from datetime import date

import pytest

from otp_tracker import symbology
from otp_tracker.symbology import ParsedSymbol, SymbolParseError, describe_safe, is_valid, parse


@pytest.mark.parametrize(
    "raw,osi",
    [
        (".AMD220715C85", "AMD220715C00085000"),
        (".AMD221021C100", "AMD221021C00100000"),
        (".JPM220422C131", "JPM220422C00131000"),
        (".JPM220916C130", "JPM220916C00130000"),
        (".QQQ220330P326", "QQQ220330P00326000"),
        (".QQQ220330P318", "QQQ220330P00318000"),
        (".QQQ220330P314", "QQQ220330P00314000"),
        # full OSI is accepted and normalised identically
        ("AMD220715C00085000", "AMD220715C00085000"),
        ("QQQ220330P00326000", "QQQ220330P00326000"),
        # optional broker prefix / whitespace / lowercase
        ("O:AMD220715C85", "AMD220715C00085000"),
        ("  .amd220715c85  ", "AMD220715C00085000"),
    ],
)
def test_parse_to_canonical_osi(raw, osi):
    parsed = parse(raw)
    assert isinstance(parsed, ParsedSymbol)
    assert parsed.osi == osi
    assert parsed.raw == raw


def test_parsed_fields_for_amd_call():
    parsed = parse(".AMD220715C85")
    assert parsed.root == "AMD"
    assert parsed.expiry == date(2022, 7, 15)
    assert parsed.right == "CALL"
    assert parsed.is_call is True
    assert parsed.strike == 85.0


def test_parsed_fields_for_qqq_put():
    parsed = parse(".QQQ220330P314")
    assert parsed.root == "QQQ"
    assert parsed.expiry == date(2022, 3, 30)
    assert parsed.right == "PUT"
    assert parsed.is_call is False
    assert parsed.strike == 314.0


def test_all_seed_symbols_parse(portfolio):
    symbols = {leg.symbol for leg in portfolio.all_legs() if leg.symbol}
    assert len(symbols) == 7
    for symbol in symbols:
        assert parse(symbol) is not None, symbol


def test_description_matches_spec_example():
    # spec §14.3: description() for C8 = "AMD Oct 21 2022 Call 100"
    assert parse(".AMD221021C100").description() == "AMD Oct 21 2022 Call 100"
    assert parse(".QQQ220330P314").description() == "QQQ Mar 30 2022 Put 314"


def test_strike_with_thousandths_via_full_osi():
    parsed = parse("SPY251219C00587500")  # 587.50
    assert parsed.strike == 587.5
    assert parsed.osi == "SPY251219C00587500"


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_blank_symbol_returns_none(raw):
    assert parse(raw) is None
    assert is_valid(raw) is True  # blank is legal (blank cell)


@pytest.mark.parametrize(
    "raw",
    [
        "NOTASYMBOL",
        "AMD220715X85",      # bad right
        "AMD221332C85",      # month 13
        "AMD220230C85",      # Feb 30
        "AMD220715C0",       # zero strike
        "AMD220715C00000000",  # zero strike in OSI form
        "AMD220715C1234567",   # 7 strike digits — ambiguous/too many
        "1AMD220715C85",       # root must start with a letter
        "AMD22071C85",         # short date
        "AMD220715C-85",       # sign not allowed
        ".AMD220715C85.0",     # decimal point not allowed
    ],
)
def test_invalid_symbols_raise(raw):
    with pytest.raises(SymbolParseError):
        parse(raw)


def test_symbol_parse_error_is_value_error():
    assert issubclass(SymbolParseError, ValueError)


def test_is_valid_probe():
    assert is_valid(".AMD220715C85") is True
    assert is_valid("NOTASYMBOL") is False
    assert is_valid(None) is True


def test_describe_safe_falls_back_to_raw_text():
    assert describe_safe(".AMD220715C85") == "AMD Jul 15 2022 Call 85"
    assert describe_safe("BROKEN") == "BROKEN"
    assert describe_safe(None) == ""


def test_root_length_limit():
    assert parse("ABCDEF220715C85").root == "ABCDEF"
    with pytest.raises(SymbolParseError):
        parse("ABCDEFG220715C85")


def test_module_exports_the_documented_names():
    assert set(symbology.__all__) == {"SymbolParseError", "ParsedSymbol", "parse", "is_valid", "describe_safe"}
