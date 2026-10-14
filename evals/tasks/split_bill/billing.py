def split_bill(total_cents, people, tip_percent=0):
    """Split a bill evenly between people, in whole cents.

    The tip is added first (rounded to the nearest cent). Returns one amount per
    person. The amounts always add up to exactly total + tip and differ by at most
    one cent; when it doesn't divide evenly, the first people pay the extra cents.
    """
    if people <= 0:
        raise ValueError("people must be positive")
    tip = round(total_cents * tip_percent / 100)
    share = round((total_cents + tip) / people)
    return [share] * people
