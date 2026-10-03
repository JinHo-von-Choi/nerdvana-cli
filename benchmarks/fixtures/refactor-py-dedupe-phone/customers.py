"""Customer registration."""


def normalize_phone(raw: str) -> str:
    """Digits only; ten digits get the +1 country code, eleven digits starting with 1 get a plus sign."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    raise ValueError(f"unusable phone number: {raw!r}")


def register(name: str, phone: str) -> dict[str, str]:
    """A customer record with a normalized phone number."""
    return {"kind": "customer", "name": name.strip(), "phone": normalize_phone(phone)}
