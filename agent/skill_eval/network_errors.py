"""
Shared network retry helpers for skill_eval model calls.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError


RETRY_WAIT_SECONDS = 6
PRIMARY_RETRY_COUNT = 5


@dataclass
class NetworkCallError(RuntimeError):
    stage: str
    model: str
    base_url: str
    attempts: int
    last_error: str
    raw_response: str = ""
    cleaned_response: str = ""

    def __init__(
        self,
        *,
        stage: str,
        model: str,
        base_url: str,
        attempts: int,
        last_error: str,
        raw_response: str = "",
        cleaned_response: str = "",
    ) -> None:
        message = (
            f"{stage} network error after {attempts} primary retries and one backup attempt "
            f"(model={model}, backup_base_url={base_url}): {last_error}"
        )
        super().__init__(message)
        self.stage = stage
        self.model = model
        self.base_url = base_url
        self.attempts = attempts
        self.last_error = last_error
        self.raw_response = raw_response
        self.cleaned_response = cleaned_response


def is_retryable_network_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError, APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in {408, 500, 502, 503, 504}
    return False
