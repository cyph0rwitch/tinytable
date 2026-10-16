import os
import uuid
from dataclasses import dataclass

import httpx


@dataclass
class Generation:
    text: str
    provider: str
    model: str
    request_id: str | None
    raw_metadata: dict


class ProviderError(Exception):
    pass


class Provider:
    name = "base"

    async def generate(self, *, system: str, messages: list[dict], model: str | None = None) -> Generation:
        raise NotImplementedError


class ManualParticipantProvider(Provider):
    name = "manual"

    async def generate(self, **kwargs):
        raise ProviderError("This participant is manual/offline; paste a message instead.")


class OpenAICompatibleProvider(Provider):
    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, default_model: str, timeout: float = 60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.timeout = timeout

    async def generate(self, *, system: str, messages: list[dict], model: str | None = None) -> Generation:
        request_id = str(uuid.uuid4())
        payload = {"model": model or self.default_model, "messages": [{"role": "system", "content": system}, *messages]}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Provider request failed: {exc}") from exc
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Provider returned an unexpected response") from exc
        return Generation(text=text, provider=self.name, model=payload["model"], request_id=data.get("id", request_id), raw_metadata={"provider_response_id": data.get("id")})


def provider_for(participant: dict) -> Provider:
    if participant["provider_type"] == "manual":
        return ManualParticipantProvider()
    config = participant["provider_config"]
    return OpenAICompatibleProvider(
        base_url=config.get("base_url", os.getenv("TINYTABLE_PROVIDER_BASE_URL", "http://localhost:11434/v1")),
        api_key=config.get("api_key", os.getenv("TINYTABLE_PROVIDER_API_KEY", "")),
        default_model=config.get("model", os.getenv("TINYTABLE_PROVIDER_MODEL", "llama3.2")),
        timeout=float(os.getenv("TINYTABLE_PROVIDER_TIMEOUT", "60")),
    )
