from csvline import parse_line


def test_quoted_field_with_several_separators():
    assert parse_line('a,"b,c,d",e') == ["a", "b,c,d", "e"]


def test_empty_quoted_field():
    assert parse_line('"",b') == ["", "b"]


def test_trailing_empty_field():
    assert parse_line("a,b,") == ["a", "b", ""]


def test_escaped_quote_next_to_separator():
    assert parse_line('"x"",y",z') == ['x",y', "z"]
