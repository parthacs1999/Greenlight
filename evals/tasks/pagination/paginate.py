def paginate(items, page, per_page=10):
    """Return the items on `page` (1-indexed) and the total number of pages."""
    if page < 1:
        raise ValueError("page must be >= 1")
    total_pages = len(items) // per_page
    start = page * per_page
    return items[start:start + per_page], total_pages
