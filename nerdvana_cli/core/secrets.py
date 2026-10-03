"""Keeping secret values out of what the model sees.

Author: 최진호
Date:   2026-10-03

Output of commands and of external tools can contain credentials: an ``env`` listing, a ``.env`` printed
by ``cat``, a token in a curl trace. Whatever reaches the model is also stored in the session transcript
and sent to the provider, so values that look like secrets are replaced by a marker before they get that far.

Two kinds of value are masked:

* the exact values of environment variables whose names look like credentials (and of the API key in use),
  wherever they appear, so a secret that matches no pattern is still caught when the process holds it;
* text with the shape of a known credential: provider and cloud keys, tokens, JWTs, PEM private keys,
  ``Authorization: Bearer`` headers and ``password=...`` style assignments.

This is a mitigation, not a boundary: a secret the process does not hold and that matches no pattern passes.
File tools are not masked, because the model must be able to read and edit files exactly as they are.

A credential a command needs for one web service does not have to be in the command's environment:
``secrets.proxy_credentials`` names an environment variable of this process per domain, and the egress
proxy (``egress_proxy.py``) adds it to the requests it forwards to that domain.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

# Name patterns for environment variables withheld from the subprocess.
#
# This is a mitigation, not a boundary. A command can still print a secret it
# reads from a file, from a credential helper, or from a variable whose name
# matches nothing here. What makes name matching worth doing at all is that
# os.environ is finite and enumerable, unlike shell syntax (see the note on
# _DANGEROUS_PATTERNS below): the cost of widening the pattern is bounded and
# the residual gap is a naming gap, not an infinite grammar.
#
# The list is deliberately name-based rather than an allowlist. An allowlist of
# permitted variables was tried on 2026-07-05 and withdrawn: it broke build
# tools and every workflow that passes custom variables through, which is most
# of them. Segment anchors ((^|[_-]) ... ([_-]|$)) keep ordinary variables such
# as PATH and TOKENIZERS_PARALLELISM out of the match.
SENSITIVE_ENV = re.compile(
    r"""(?ix)
    (?: api[_-]?key
      | (?:^|[_-]) key (?:[_-]|$)
      | private[_-]?key
      | access[_-]?key
      | secret
      | passw
      | passphrase
      | credential
      | (?:^|[_-]) token (?:[_-]|$)
      | (?:^|[_-]) pat (?:[_-]|$)
      | (?:^|[_-]) (?:pem|dsn|bearer|authorization) (?:[_-]|$)
      | (?:^|[_-]) (?:database|db|redis|mongo|mongodb|postgres|postgresql|mysql|amqp|rabbitmq)
        [_-]? (?:url|uri|dsn|conn|connection(?:[_-]?string)?)
      )
    """
)

# Environment values shorter than this are not masked: a four-character value matches innocent text.
MIN_VALUE_LENGTH = 8
MARKER           = "[REDACTED]"

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key",  re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)),
    ("api-key",      re.compile(r"\bsk-(?:ant-|proj-|or-)?[A-Za-z0-9_-]{20,}")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}")),
    ("aws-key-id",   re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google-key",   re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack-token",  re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("jwt",          re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
)
_BEARER = re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]{20,}")
# name = value where the name says the value is a credential; only the value is replaced.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (\b[A-Za-z0-9_.-]*(?:password|passwd|passphrase|secret|api[_-]?key|access[_-]?key|private[_-]?key|token)\b
     ["']?\s*[:=]\s*["']?)
    ([^\s"'`,;&]{8,})
    """
)


@dataclass(frozen=True)
class MaskResult:
    """The masked text and how many values were replaced."""

    text:  str
    count: int


class SecretMasker:
    """Replaces secret values in text; build one per session and reuse it."""

    def __init__(self, values: Mapping[str, str] | None = None, extra_patterns: Iterable[str] = ()) -> None:
        # Longest first, so a value that contains another is replaced whole.
        self._values = sorted(
            ((name, value) for name, value in (values or {}).items() if len(value) >= MIN_VALUE_LENGTH),
            key=lambda pair: -len(pair[1]),
        )
        self._extra = [re.compile(pattern) for pattern in extra_patterns]

    @classmethod
    def from_environment(cls, extra_values: Mapping[str, str] | None = None, extra_patterns: Iterable[str] = ()) -> SecretMasker:
        """A masker holding the values of this process's credential-named environment variables."""
        values = {name: value for name, value in os.environ.items() if SENSITIVE_ENV.search(name)}
        values.update(extra_values or {})
        return cls(values, extra_patterns)

    def mask(self, text: str) -> MaskResult:
        """*text* with secret values replaced by ``[REDACTED]``."""
        count = 0
        for name, value in self._values:
            if value in text:
                count += text.count(value)
                text = text.replace(value, f"{MARKER}:{name}")
        for label, pattern in _PATTERNS:
            text, found = pattern.subn(f"{MARKER}:{label}", text)
            count += found
        text, found = _BEARER.subn(lambda m: f"{m.group(1)} {MARKER}", text)
        count += found
        text, found = _ASSIGNMENT.subn(lambda m: m.group(1) + MARKER, text)
        count += found
        for pattern in self._extra:
            text, found = pattern.subn(MARKER, text)
            count += found
        return MaskResult(text, count)


_HEADER_NAME   = re.compile(r"^[A-Za-z0-9-]+$")
_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ProxyCredential:
    """A credential the egress proxy adds to requests for one domain pattern.

    The spec of ``secrets.proxy_credentials`` is ``VARIABLE`` (sent as ``Authorization: Bearer <value>``) or
    ``Header-Name:VARIABLE`` (the value is sent as it is, so a Basic or API-key header carries its own prefix).
    """

    domain:   str
    header:   str
    variable: str
    bearer:   bool

    @classmethod
    def parse(cls, domain: str, spec: str) -> ProxyCredential:
        """Read one ``domain: spec`` entry; ValueError when the spec is not well formed."""
        header, colon, variable = spec.strip().rpartition(":")
        if not _VARIABLE_NAME.match(variable):
            raise ValueError(f"'{spec}' must be VARIABLE or Header-Name:VARIABLE with an environment variable name")
        if colon and not _HEADER_NAME.match(header):
            raise ValueError(f"'{header}' is not a header name")
        return cls(domain, header if colon else "Authorization", variable, not colon)

    def header_pair(self, environ: Mapping[str, str]) -> tuple[str, str] | None:
        """The (name, value) to add, or None when the variable is unset, empty or not safe to send in a header."""
        value = environ.get(self.variable, "")
        if not value or any(char in value for char in "\r\n\0"):
            return None
        return self.header, f"Bearer {value}" if self.bearer else value
