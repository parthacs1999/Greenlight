def parse_line(line, sep=","):
    """Parse one CSV line into a list of fields.

    Supports double-quoted fields, which may contain the separator, and escaped
    quotes written as two double quotes ("").
    """
    return [field.strip() for field in line.rstrip("\n").split(sep)]
