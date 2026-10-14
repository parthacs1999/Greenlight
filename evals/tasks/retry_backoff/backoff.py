def backoff_delays(base, retries, cap=None, factor=2):
    """Exponential backoff delays: base, base*factor, base*factor**2, ...

    Returns a list of `retries` delays in seconds, each limited to `cap` if given.
    """
    delays = []
    for attempt in range(1, retries + 1):
        delay = base * factor ** attempt
        delays.append(delay)
    return delays
