"""Typed contract shared by the Codex Chrome lifecycle tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Handshake(BaseModel):
    """Compatibility data returned by the provider."""

    bridge_version: str
    extension_version: str
    profile_identity: str
    capabilities: list[str]


class Remediation(BaseModel):
    """One safe action that can resolve a provider failure."""

    operation: str
    reason: str


class ProviderError(BaseModel):
    """Stable provider failure information."""

    code: str
    message: str
    retryable: bool = False
    remediation: list[Remediation] = Field(default_factory=list)


class Output(BaseModel):
    """Common lifecycle result."""

    ok: bool
    status: Literal["active", "missing", "failed", "closed", "detached"]
    profile_binding: str
    ownership: Literal["started", "attached"] | None = None
    session_id: str | None = None
    tab_id: str | None = None
    handshake: Handshake | None = None
    provider_event_id: str
    error: ProviderError | None = None
