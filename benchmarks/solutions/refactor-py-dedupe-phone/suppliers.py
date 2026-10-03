"""Supplier registration."""

from phone import normalize_phone


def register(name: str, phone: str, terms_days: int = 30) -> dict[str, object]:
    """A supplier record with a normalized phone number and payment terms."""
    return {"kind": "supplier", "name": name.strip(), "phone": normalize_phone(phone), "terms_days": terms_days}
