from __future__ import annotations

import logging
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

ActivityListener = Callable[[dict[str, Any]], None]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ActivityTrace:
    """A bounded, safe-to-display trace of one LMCTL request."""

    def __init__(
        self,
        request_id: str | None = None,
        max_events: int = 64,
        enabled: bool = True,
        listener: ActivityListener | None = None,
    ) -> None:
        self.request_id = request_id or f"lmctl-{uuid.uuid4().hex}"
        self.max_events = max(1, max_events)
        self.enabled = enabled
        self._events: list[dict[str, Any]] = []
        self._listeners: list[ActivityListener] = []
        if listener is not None:
            self._listeners.append(listener)

    def subscribe(self, listener: ActivityListener) -> None:
        self._listeners.append(listener)

    def emit(self, event: str, message: str, **fields: Any) -> dict[str, Any] | None:
        if not self.enabled or len(self._events) >= self.max_events:
            return None
        payload: dict[str, Any] = {
            "id": self.request_id,
            "type": "lmctl.activity",
            "event": event,
            "message": message,
            "sequence": len(self._events) + 1,
            "timestamp": _now(),
        }
        payload.update({key: value for key, value in fields.items() if value is not None})
        self._events.append(payload)
        logger.info("LMCTL activity: %s", message)
        for listener in tuple(self._listeners):
            try:
                listener(dict(payload))
            except Exception:
                logger.warning("LMCTL activity listener failed", exc_info=True)
        return payload

    def events(self) -> list[dict[str, Any]]:
        return [dict(event) for event in self._events]


class ActivityStore:
    """Keep a small in-memory history for CLI/API diagnostics."""

    def __init__(self, max_runs: int = 20) -> None:
        self._runs: deque[dict[str, Any]] = deque(maxlen=max(1, max_runs))

    def record(self, trace: ActivityTrace) -> None:
        events = trace.events()
        if not events:
            return
        self._runs.append(
            {
                "request_id": trace.request_id,
                "completed_at": _now(),
                "events": events,
            }
        )

    def latest(self) -> dict[str, Any] | None:
        return dict(self._runs[-1]) if self._runs else None

    def list(self) -> list[dict[str, Any]]:
        return [dict(run) for run in self._runs]
