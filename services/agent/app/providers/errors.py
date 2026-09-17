from __future__ import annotations

import re

import httpx


def provider_error_detail(exc: Exception) -> str:
    """Keep the upstream status and bounded error message, never its request/body."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return type(exc).__name__
    detail = f"{type(exc).__name__}: upstream HTTP {exc.response.status_code}"
    try:
        payload = exc.response.json()
    except ValueError:
        return detail
    error = payload.get("error") if isinstance(payload, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    if isinstance(message, str) and message.strip():
        message = re.sub(r"(?i)\b(bearer|basic)\s+[^\s,;]+", r"\1 <redacted>", message)
        message = re.sub(
            r"(?i)\b(api[_-]?key|token|password|secret)\s*[=:]\s*[^\s,;]+",
            r"\1=<redacted>",
            message,
        )
        detail += ": " + " ".join(message.split())[:800]
    return detail
