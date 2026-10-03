"""Reasoning effort per phase of a run: planning, implementation and verification.

Author: 최진호
Date:   2026-10-03

``model.effort_planning``, ``model.effort_implementation`` and ``model.effort_verification`` each name the
effort one phase runs at; an empty one means ``model.reasoning_effort``. With none of them set nothing here
touches the provider.

How a level reaches the model depends on the provider:

* Anthropic (``set_turn_effort``): the provider changes the level between turns where the model keeps its
  prompt cache while doing so, and otherwise holds the level it started with; a change it cannot make is
  not forced, because it would restart the cache.
* Providers that read ``config.reasoning_effort`` on every request (OpenAI, Gemini): the loop sets that field
  for the phase and puts the configured value back when the run ends.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nerdvana_cli.core.config.settings import ModelConfig

if TYPE_CHECKING:
    from nerdvana_cli.core.loop.agent_loop import AgentLoop

logger = logging.getLogger(__name__)

PLANNING       = "planning"
IMPLEMENTATION = "implementation"
VERIFICATION   = "verification"


def phase_level(model: ModelConfig, phase: str) -> str:
    """The reasoning effort *phase* runs at: its own setting, else ``model.reasoning_effort``."""
    own = {PLANNING: model.effort_planning, IMPLEMENTATION: model.effort_implementation, VERIFICATION: model.effort_verification}[phase]
    return own or model.reasoning_effort


def phases_configured(model: ModelConfig) -> bool:
    """True when at least one phase has an effort of its own."""
    return bool(model.effort_planning or model.effort_implementation or model.effort_verification)


class PhaseEffort:
    """Moves the provider of *loop* to the effort of the phase the run is in."""

    def __init__(self, loop: AgentLoop) -> None:
        self._loop   = loop
        self._phase  = ""
        self._warned = False

    def enter(self, phase: str) -> None:
        """Run the requests that follow at the effort of *phase*."""
        model = self._loop.settings.model
        if phase == self._phase or not phases_configured(model):
            return
        self._phase = phase
        self._apply(phase_level(model, phase))

    def reapply(self) -> None:
        """Carry the phase over to a provider that was just created, after a model switch."""
        if self._phase:
            self._apply(phase_level(self._loop.settings.model, self._phase))

    def restore(self) -> None:
        """At the end of a run, give a provider that reads its config the configured effort back."""
        if self._phase and not hasattr(self._loop.provider, "set_turn_effort"):
            self._set_config(self._loop.settings.model.reasoning_effort)
        self._phase = ""

    def _apply(self, level: str) -> None:
        provider = self._loop.provider
        setter   = getattr(provider, "set_turn_effort", None)
        if setter is None:
            self._set_config(level)
            return
        if not level:
            return
        try:
            if not setter(level):
                logger.debug("effort %s not applied: the model holds the level it started with", level)
        except ValueError as exc:
            self._warn(str(exc))

    def _set_config(self, level: str) -> None:
        config = getattr(self._loop.provider, "config", None)
        if config is not None and hasattr(config, "reasoning_effort"):
            config.reasoning_effort = level

    def _warn(self, reason: str) -> None:
        """Say once per session that a phase effort the model does not accept was left out."""
        if not self._warned:
            self._warned = True
            logger.warning("phase effort not applied: %s", reason)
