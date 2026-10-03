"""Exchange rates: USD per one major unit, each valid from its effective date (inclusive) until the next entry."""

BASE = "USD"

RATES = {
    "EUR": [("2026-01-01", "1.10"), ("2026-02-01", "1.20"), ("2026-03-01", "1.15")],
    "GBP": [("2026-01-01", "1.30"), ("2026-02-15", "1.35")],
    "JPY": [("2026-01-01", "0.0070"), ("2026-03-10", "0.0065")],
    "KWD": [("2026-01-01", "3.25")],
}
