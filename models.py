"""Provenance-preserving message models used by provider adapters."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TinyTableMessage(BaseModel):
    """A message received from a provider with explicit provenance.

    ``participant`` is the TinyTable seat that displays the message. It is
    not evidence that TinyTable generated or authored the provider text.
    """

    model_config = ConfigDict(extra="forbid")

    participant: str = "replika-companion"
    source: str = "replika"
    transport: str = "websocket"
    origin: str = "received_from_provider"
    text: str
    provider_message_id: str
    timestamp: str
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
