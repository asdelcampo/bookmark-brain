"""LLM client — targets llama-server (llama.cpp's OpenAI-compatible API)."""
from __future__ import annotations

import httpx
from openai import OpenAI, APIConnectionError, APIStatusError

# Import the config module (not individual values) so runtime overrides
# (e.g. --model flag setting bb.config.LLM_MODEL) are picked up at call time.
from bb import config as _cfg


class LLMError(RuntimeError):
    """Base class for all LLM client errors."""


class LLMConnectionError(LLMError):
    """Raised when llama-server is not reachable."""


class LLMHTTPError(LLMError):
    """Raised when llama-server returns an HTTP error status."""


# Keep old names as aliases so any remaining internal references don't break
OllamaError = LLMError
OllamaConnectionError = LLMConnectionError
OllamaHTTPError = LLMHTTPError


def _client() -> OpenAI:
    return OpenAI(base_url=_cfg.LLM_BASE_URL, api_key="not-needed")


def chat(
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float | None = None,
    num_predict: int | None = None,
) -> str:
    """
    Send a chat completion request to llama-server.
    Returns the assistant reply string.
    """
    if model is None:
        model = _cfg.LLM_MODEL

    kwargs: dict = {"model": model, "messages": messages}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if num_predict is not None:
        kwargs["max_tokens"] = num_predict

    try:
        response = _client().chat.completions.create(**kwargs)
        return (response.choices[0].message.content or "").strip()
    except APIConnectionError as exc:
        raise LLMConnectionError(
            f"Cannot reach llama-server at {_cfg.LLM_BASE_URL}. "
            "Is llama-server running on port 8001?"
        ) from exc
    except APIStatusError as exc:
        raise LLMHTTPError(
            f"llama-server HTTP {exc.status_code}: {str(exc.body)[:200]}"
        ) from exc


def generate(prompt: str, system: str | None = None, model: str | None = None) -> str:
    """
    Convenience wrapper: single prompt → assistant reply.
    Internally uses the chat completion endpoint.
    """
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return chat(messages, model=model)


def is_available(model: str | None = None) -> bool:
    """Return True if llama-server is reachable and responding."""
    health_url = _cfg.LLM_BASE_URL.rstrip("/v1").rstrip("/") + "/health"
    try:
        r = httpx.get(health_url, timeout=5)
        return r.status_code < 400
    except httpx.HTTPError:
        return False
