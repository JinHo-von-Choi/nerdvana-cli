"""Pricing table: USD cost of a provider request from the vendor rates in ``providers/pricing.yml``.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PricingTable:
    """Loads provider/model pricing from ``providers/pricing.yml``.

    Rates are USD per 1,000,000 tokens (``input_per_1m`` / ``output_per_1m``),
    matching how vendors publish them.

    Provides ``estimate_cost(provider, model, input_tokens, output_tokens)``.
    Falls back to 0.0 when a model is not in the table.
    """

    def __init__(self, pricing_path: Path | None = None) -> None:
        self._prices: dict[str, dict[str, dict[str, float]]] = {}
        path = pricing_path or self._default_path()
        self._load(path)

    @staticmethod
    def _default_path() -> Path:
        """Resolve the bundled pricing.yml next to this package."""
        # nerdvana_cli/providers/pricing.yml
        pkg_root = Path(__file__).parents[3]  # nerdvana_cli/
        return pkg_root / "providers" / "pricing.yml"

    def _load(self, path: Path) -> None:
        try:
            import yaml  # type: ignore[import-untyped,unused-ignore]
            with open(path) as f:
                raw: dict[str, Any] = yaml.safe_load(f) or {}
            for provider, models in raw.items():
                if not isinstance(models, dict):
                    continue
                self._prices[provider.lower()] = {
                    m.lower(): {k: float(v) for k, v in info.items()}
                    for m, info in models.items()
                    if isinstance(info, dict)
                }
        except FileNotFoundError:
            logger.debug("pricing.yml not found at %s; all costs default to 0.0", path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load pricing.yml: %s", exc)

    def estimate_cost(
        self,
        provider:           str,
        model:              str,
        input_tokens:       int,
        output_tokens:      int,
        cache_read_tokens:  int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """Return estimated USD cost. Returns 0.0 for unknown models.

        ``input_tokens`` is the whole prompt; the cached part is billed at the
        entry's ``cache_read_per_1m`` / ``cache_write_per_1m`` and the rest at
        ``input_per_1m``. An entry without cache rates bills cached tokens at the
        plain input rate, which is the figure before caching existed.
        """
        info = self._prices.get(provider.lower(), {}).get(model.lower(), {})
        if not info:
            return 0.0
        input_rate = info.get("input_per_1m", 0.0)
        read_rate  = info.get("cache_read_per_1m",  input_rate)
        write_rate = info.get("cache_write_per_1m", input_rate)
        fresh      = max(input_tokens - cache_read_tokens - cache_write_tokens, 0)
        input_cost = (
            input_rate * fresh
            + read_rate  * cache_read_tokens
            + write_rate * cache_write_tokens
        ) / 1_000_000.0
        output_cost = info.get("output_per_1m", 0.0) * output_tokens / 1_000_000.0
        return input_cost + output_cost

    def has_price(self, provider: str, model: str) -> bool:
        """True when the table has a rate for *model* under *provider*."""
        return bool(self._prices.get(provider.lower(), {}).get(model.lower()))

    def known_providers(self) -> list[str]:
        return list(self._prices.keys())

    def known_models(self, provider: str) -> list[str]:
        return list(self._prices.get(provider.lower(), {}).keys())
