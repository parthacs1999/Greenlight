import pytest

from config import parse_config


def test_booleans_any_case():
    assert parse_config("a = FALSE\nb = true\nc = TrUe") == {"a": False, "b": True, "c": True}


def test_strings_that_are_not_numbers():
    assert parse_config("v = 1.5\nw = 12abc\nx = a#b") == {"v": "1.5", "w": "12abc", "x": "a#b"}


def test_later_keys_override_and_whitespace():
    assert parse_config("  k =  one  \nk=2") == {"k": 2}


def test_multiple_equals_and_comment():
    assert parse_config("expr = a=b=c  # note") == {"expr": "a=b=c"}


def test_error_line_number_counts_blank_and_comment_lines():
    with pytest.raises(ValueError, match="line 4"):
        parse_config("# header\n\nok = 1\noops\n")
