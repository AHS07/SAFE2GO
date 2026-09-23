"""DeepSeek chat client (explanations only, never in a safety or analytics path).

Timeout and retry come from thresholds.yaml (10 s, one retry). Timeouts,
network errors, and 5xx responses are retried; any failure after that, or
a 4xx such as a bad key, raises LLMUnavailableError so the caller can fall
back to retrieval results.
"""
from __future__ import annotations

import logging
from functools import lru_cache

import httpx

from app.config.settings import get_settings
from app.config.thresholds import get_thresholds
from app.core.errors import LLMUnavailableError

log = logging.getLogger("safe2go.deepseek")

_SERVER_ERROR = 500


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        retries: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._retries = retries
        self._transport = transport

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, system: str, user: str, max_tokens: int, temperature: float) -> str:
        body = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        last_error = "no attempt made"
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout, transport=self._transport
        ) as client:
            for attempt in range(self._retries + 1):
                try:
                    response = await client.post("/chat/completions", json=body, headers=headers)
                except httpx.HTTPError as exc:
                    last_error = type(exc).__name__
                    log.warning("DeepSeek request failed", extra={"attempt": attempt + 1, "error": last_error})
                    continue
                if response.status_code >= _SERVER_ERROR:
                    last_error = f"HTTP {response.status_code}"
                    log.warning("DeepSeek server error", extra={"attempt": attempt + 1, "status": response.status_code})
                    continue
                if response.is_error:
                    log.error("DeepSeek rejected the request", extra={"status": response.status_code})
                    raise LLMUnavailableError("The explanation service rejected the request.")
                return _content(response)
        raise LLMUnavailableError(
            "The explanation service did not answer in time.", details={"last_error": last_error}
        )


def _content(response: httpx.Response) -> str:
    try:
        return response.json()["choices"][0]["message"]["content"].strip()
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        log.error("DeepSeek response had an unexpected shape")
        raise LLMUnavailableError("The explanation service returned an unreadable answer.") from exc


@lru_cache
def get_deepseek_client() -> DeepSeekClient | None:
    """The configured client, or None when DEEPSEEK_API_KEY is not set."""
    settings = get_settings()
    if not settings.deepseek_enabled:
        return None
    cfg = get_thresholds().assistant
    return DeepSeekClient(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout_seconds=cfg.explain_timeout_seconds,
        retries=cfg.explain_retries,
    )
