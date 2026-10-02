"""Choosing the model a sub-agent runs on.

Author: 최진호
Date:   2026-10-03

A sub-agent inherits its parent's model unless something names another one. In
order of precedence: the ``model`` argument of the Agent tool, the ``model`` of
the agent definition, then the model mapped to a category, where the category
comes from the ``category`` argument or the agent definition. A model is written
``model`` for the parent's provider or ``provider:model`` for another one, the
same form as ``model.fallback_models``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from nerdvana_cli.core.provider_recovery import parse_fallback
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.providers.base import ProviderName

logger = logging.getLogger(__name__)


def select_model(model_arg: str, category_arg: str, definition_model: str, definition_category: str, categories: Mapping[str, str]) -> str:
    """The model spec to run on, empty when the sub-agent should inherit its parent's."""
    for spec in (model_arg, definition_model):
        if spec:
            return spec
    return categories.get(category_arg or definition_category, "")


def point_settings_at(settings: NerdvanaSettings, provider: str | None, model: str) -> bool:
    """Point *settings* at *model*, on *provider* when one is given.

    A provider change picks up that provider's credential from the environment and
    drops the base URL, which belongs to the previous provider. Returns False and
    leaves *settings* alone when the other provider has no credential.
    """
    if provider and provider != settings.model.provider:
        from nerdvana_cli.providers.factory import resolve_api_key

        api_key = resolve_api_key(ProviderName(provider))
        if not api_key:
            return False
        settings.model.provider = provider
        settings.model.api_key  = api_key
        settings.model.base_url = ""
    settings.model.model = model
    return True


def apply_model_spec(settings: NerdvanaSettings, spec: str) -> bool:
    """Route *settings* to the model named by *spec*; an empty spec changes nothing."""
    if not spec:
        return False
    provider, model = parse_fallback(spec)
    if point_settings_at(settings, provider, model):
        return True
    logger.warning("model %r skipped: no credential for provider %r, the parent's model is used", spec, provider)
    return False
