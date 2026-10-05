"""Evidence schemas for the task contract: repositories, commands, policy and receipts.

A contract names the repository a task works in, the command that proves the work and the
policy it has to stay inside. A verification receipt is what one command produced, carrying
the digest of the contract that asked for it, so the receipt can be checked later against
exactly the contract it claims to come from.

Author: 최진호
Date:   2026-10-05
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TargetRepo(BaseModel):
    """One repository a contract applies to, pinned to the commit the work starts from."""

    path:            str
    base_commit_sha: str


class VerificationCommand(BaseModel):
    """One command a contract requires to pass, with its own timeout and extra environment."""

    command: str
    timeout: int                 = 300
    env:     dict[str, str]      = Field(default_factory=dict)


class PolicyEnvelope(BaseModel):
    """The limits the work behind a contract has to stay inside."""

    allowed_models:  list[str] = Field(default_factory=list)
    max_total_tokens: int      = 0
    max_cost_usd:    float     = 0.0


class VerificationReceipt(BaseModel):
    """The evidence one executed verification command left behind.

    ``contract_digest`` and ``test_digest_manifest`` bind the receipt to the contract and to
    the test suite that was pinned when the command ran, so a receipt that no longer matches
    either of them cannot be read as proof of the contract it names.
    """

    contract_digest:      str
    test_digest_manifest: dict[str, str]
    command:              str
    returncode:           int
    output_tail:          str
    passed:               bool
    duration_seconds:     float
    timestamp:            str
