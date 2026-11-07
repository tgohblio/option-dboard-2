"""Number-format parity with Excel (spec §9, §14.1)."""

from __future__ import annotations

from datetime import date

import pytest

from otp_tracker import formatting
from otp_tracker.formatting import BLACK, BLUE, DEFAULT, PURPLE, RED, col_color, format_cell


# --------------------------------------------------------------------------- #
# money: [Color10]#,###.00;([Red]#,###.00);[Black]0.0
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value,text,color",
    [
        (755, "755.00", BLUE),
        (0.51, "0.51", BLUE),
        (-108, "(108.00)", RED),
        (-650, "(650.00)", RED),
        (0, "0.0", BLACK),
        (1234.5, "1,234.50", BLUE),
        (-4626, "(4,626.00)", RED),
        (-0.0018, "(0.00)", RED),
    ],
)
def test_money_format(value, text, color):
    assert format_cell(value, "money") == (text, color)


def test_money_zero_uses_excel_black_zero_rule():
    assert format_cell(0.0, "money")[0] == "0.0"
    assert format_cell(-0.0, "money")[0] == "0.0"


# --------------------------------------------------------------------------- #
# money_simple: #,##0.00 (negative sign in front, no parens)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value,text,color",
    [
        (2947, "2,947.00", DEFAULT),
        (-1170, "-1,170.00", RED),
        (0, "0.00", DEFAULT),
        (28.45, "28.45", DEFAULT),
    ],
)
def test_money_simple_format(value, text, color):
    assert format_cell(value, "money_simple") == (text, color)


# --------------------------------------------------------------------------- #
# qty: integer rendering, parens for shorts, 0.0 for zero
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value,text,color",
    [
        (500, "500", BLUE),
        (2500, "2,500", BLUE),
        (-100, "(100)", RED),
        (-1000, "(1,000)", RED),
        (0, "0.0", BLACK),
        (1, "1", BLUE),
    ],
)
def test_qty_format(value, text, color):
    assert format_cell(value, "qty") == (text, color)


# --------------------------------------------------------------------------- #
# dollar / percent / num / date / bool / text
# --------------------------------------------------------------------------- #
def test_dollar_format():
    assert format_cell(605, "dollar") == ("$605.00", BLUE)
    assert format_cell(-108, "dollar") == ("$-108.00", RED)
    assert format_cell(0, "dollar") == ("$0.00", DEFAULT)


@pytest.mark.parametrize(
    "value,text",
    [
        (-1.00980873, "-100.98%"),   # Z10 — below -100%, never clamped
        (-0.001819284415, "-0.18%"),  # Z6
        (-0.8388235294, "-83.88%"),   # Z8
        (-1, "-100.00%"),             # Z13
        (0.1234, "12.34%"),
        (0, "0.00%"),
    ],
)
def test_percent_format_and_no_clamping(value, text):
    assert format_cell(value, "percent")[0] == text


def test_percent_colour_follows_sign():
    assert format_cell(-1.00980873, "percent")[1] == RED
    assert format_cell(0.12, "percent")[1] == BLUE
    assert format_cell(0, "percent")[1] == BLACK


@pytest.mark.parametrize(
    "value,text",
    [(78.205, "78.205"), (0.563, "0.563"), (11.0, "11"), (2.46, "2.46"), (-3.1, "-3.1")],
)
def test_num_format(value, text):
    assert format_cell(value, "num")[0] == text


def test_date_format():
    assert format_cell(date(2021, 12, 18), "date")[0] == "12/18/2021"
    assert format_cell(date(2022, 3, 8), "date")[0] == "3/8/2022"


def test_bool_format():
    assert format_cell(True, "bool") == ("YES", formatting.GREEN)
    assert format_cell(False, "bool") == ("NO", RED)


def test_text_format_is_as_is():
    assert format_cell("Diagonal", "text") == ("Diagonal", DEFAULT)
    assert format_cell("OTP PORFOLIO TRACKER - OPEN TRADES", "text")[0].startswith("OTP PORFOLIO")


# --------------------------------------------------------------------------- #
# None / blank / purple override
# --------------------------------------------------------------------------- #
def test_none_renders_em_dash():
    assert format_cell(None, "money") == ("—", DEFAULT)
    assert format_cell(None, "percent") == ("—", DEFAULT)


def test_blank_string_stays_blank():
    assert format_cell("   ", "money") == ("", DEFAULT)


@pytest.mark.parametrize(
    "value,fmt",
    [(755, "money"), (-108, "money"), (0, "money"), (-1.0098, "percent"), (500, "qty"), (12.5, "num")],
)
def test_purple_override_wins_over_sign_colour(value, fmt):
    assert format_cell(value, fmt, purple=True)[1] == PURPLE
    assert PURPLE == "#FF7E3794"


def test_unknown_format_falls_back_to_text():
    assert format_cell("x", "unknown-format")[0] == "x"


def test_col_color_helper():
    assert col_color(1) == BLUE
    assert col_color(-1) == RED
    assert col_color(0) == BLACK


def test_numeric_strings_are_coerced_by_formatters():
    assert format_cell("755", "money")[0] == "755.00"
    assert format_cell(" -108 ", "money")[0] == "(108.00)"
