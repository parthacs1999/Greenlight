from csvline import parse_line


def test_simple():
    assert parse_line("a,b,c") == ["a", "b", "c"]


def test_quoted_separator():
    assert parse_line('1,"Smith, John",42') == ["1", "Smith, John", "42"]


def test_escaped_quote():
    assert parse_line('"say ""hi""",x') == ['say "hi"', "x"]


def test_empty_fields():
    assert parse_line("a,,c") == ["a", "", "c"]


def test_custom_separator():
    assert parse_line('a;"b;c"', sep=";") == ["a", "b;c"]


def test_trailing_newline():
    assert parse_line("x,y\n") == ["x", "y"]
