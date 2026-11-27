"""Run top-level database coroutines with a driver-compatible event loop."""

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any, TypeVar


_Result = TypeVar("_Result")


def _windows_selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Create a Selector loop for Psycopg without changing the global policy."""
    loop = asyncio.SelectorEventLoop()
    # asyncio.Runner requires custom loop factories to set the loop for this
    # thread. The wrapper below clears it after the runner closes the loop.
    asyncio.set_event_loop(loop)
    return loop


def run_with_database_event_loop(coroutine: Coroutine[Any, Any, _Result]) -> _Result:
    """Run a top-level coroutine using the PostgreSQL driver's platform loop.

    Psycopg's async API requires SelectorEventLoop on Windows. This chooses
    that loop only for this top-level run; it does not mutate the process-wide
    event-loop policy or affect Linux/macOS defaults.
    """
    if sys.platform != "win32":
        return asyncio.run(coroutine)

    try:
        return asyncio.run(coroutine, loop_factory=_windows_selector_loop_factory)
    finally:
        asyncio.set_event_loop(None)
