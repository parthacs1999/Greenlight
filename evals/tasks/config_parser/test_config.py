import pytest

from config import parse_config


def test_types():
    text = "name = demo\nport = 8080\ndebug = True\noffset = -3\n"
    assert parse_config(text) == {"name": "demo", "port": 8080, "debug": True, "offset": -3}


def test_value_containing_equals():
    assert parse_config("url = https://x.test/?a=1&b=2") == {"url": "https://x.test/?a=1&b=2"}


def test_comments():
    text = "# settings\n\nretries = 3  # be careful\n"
    assert parse_config(text) == {"retries": 3}


def test_error_mentions_line_number():
    with pytest.raises(ValueError, match="line 2"):
        parse_config("a = 1\nnot a setting\n")
