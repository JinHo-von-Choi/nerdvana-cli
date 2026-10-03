"""Customer registration."""

from phone import normalize_phone


def register(name: str, phone: str) -> dict[str, str]:
    """A customer record with a normalized phone number."""
    return {"kind": "customer", "name": name.strip(), "phone": normalize_phone(phone)}
