"""Small intentionally incomplete slug utility."""


def slugify(value: str) -> str:
    return "-".join(value.strip().lower().split())
