def parse_config(text):
    """Parse simple `key = value` config text into a dict.

    Rules:
    - Blank lines and lines starting with '#' are ignored.
    - ' #' (a space then '#') after a value starts an inline comment.
    - Keys and values are stripped of surrounding whitespace. Only the first '='
      separates key from value; later '=' characters belong to the value.
    - true/false in any letter case become bools, integers (including negative ones)
      become ints, and everything else stays a string.
    - A later key overrides an earlier one.
    - A non-blank, non-comment line without '=' raises ValueError that mentions its
      1-based line number, e.g. "line 3: expected key = value".
    """
    result = {}
    for number, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"line {number}: expected key = value")
        key, value = line.split("=")
        result[key.strip()] = _convert(value.strip())
    return result


def _convert(value):
    if value.lower() in ("true", "false"):
        return value == "true"
    if value.isdigit():
        return int(value)
    return value
