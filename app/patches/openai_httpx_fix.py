"""
Runtime patch for OpenAI's AsyncHttpxClientWrapper.

Recent versions of httpx (>=0.28) expect the AsyncClient instance to have a
``_state`` attribute when closing. The OpenAI client schedules an asynchronous
``aclose`` inside ``__del__``; during interpreter shutdown or GC this happens
after httpx has already cleared the attribute, leading to noisy
``AttributeError: '_state'`` traces.

We override ``__del__`` to only attempt the async close when the attribute is
present, silently skipping otherwise. This preserves the clean shutdown path
while avoiding the unhandled exception spam in the logs.
"""

from __future__ import annotations

import asyncio
from typing import Any

try:
    from openai._base_client import AsyncHttpxClientWrapper  # type: ignore
except Exception:  # pragma: no cover - import guard
    AsyncHttpxClientWrapper = None  # type: ignore[misc]


def _safe_async_client_del(self: Any) -> None:
    """Best-effort async cleanup without raising on missing state."""
    if self is None or not hasattr(self, "_state"):
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (e.g. interpreter shutdown); nothing we can do.
        return

    try:
        loop.create_task(self.aclose())
    except Exception:
        # Avoid raising during GC – original behaviour was already best-effort.
        return


if AsyncHttpxClientWrapper is not None:  # pragma: no cover - defensive branch
    AsyncHttpxClientWrapper.__del__ = _safe_async_client_del  # type: ignore[assignment]

