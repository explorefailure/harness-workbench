def slugify(text):
    """Return a URL-safe identifier for a short ASCII label."""
    return text.lower().replace(" ", "-")
