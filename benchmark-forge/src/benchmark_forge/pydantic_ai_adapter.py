"""Small optional bridge from PydanticAI agents to the role contracts.

The core package remains testable without importing PydanticAI. Production role
implementations can wrap an ``Agent`` and return the Pydantic models declared in
``actions.py``. This adapter deliberately does not own Benchmark state changes.
"""
from __future__ import annotations

import json
import signal
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, Iterator, TypeVar

T = TypeVar("T")


_CURRENT_TELEMETRY: ContextVar["RunTelemetry | None"] = ContextVar("benchmark_forge_telemetry", default=None)


class RunTelemetry:
    """Append-only, secret-free invocation telemetry for formal runs."""

    def __init__(self, path: str | Path, *, run_id: str):
        self.path = Path(path)
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, *, label: str, output_type: Any, status: str, duration_seconds: float,
               error: str | None = None) -> None:
        payload = {
            "run_id": self.run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "label": label,
            "output_type": getattr(output_type, "__name__", str(output_type)),
            "status": status,
            "duration_seconds": round(duration_seconds, 3),
        }
        if error:
            payload["error"] = error[:2_000]
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


@contextmanager
def telemetry_scope(path: str | Path, *, run_id: str) -> Iterator[RunTelemetry]:
    telemetry = RunTelemetry(path, run_id=run_id)
    token = _CURRENT_TELEMETRY.set(telemetry)
    try:
        yield telemetry
    finally:
        _CURRENT_TELEMETRY.reset(token)


class _HardWallClockTimeout(BaseException):
    """Internal BaseException so model SDK retry loops cannot swallow the deadline."""


def _run_with_wall_clock_timeout(call, timeout: float | None):
    """Apply one total deadline around a synchronous Agent run on Unix main threads.

    Provider timeouts are per HTTP request and structured-output repair can issue
    multiple requests. Without this guard a nominal 90-second component call was
    observed taking 267 seconds.
    """
    if timeout is None or timeout <= 0 or threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
        return call()
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def expired(signum, frame):
        raise _HardWallClockTimeout()

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        return call()
    except _HardWallClockTimeout:
        raise TimeoutError(f"LLM call exceeded total wall-clock timeout of {timeout} seconds")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


class PydanticAIUnavailable(RuntimeError):
    pass


def openai_compatible_model(*, model_name: str, base_url: str, api_key: str):
    """Build an OpenAI-compatible PydanticAI model without storing credentials."""
    try:
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency
        raise PydanticAIUnavailable(
            "Install benchmark-forge to use the PydanticAI OpenAI-compatible adapter."
        ) from exc
    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(base_url=base_url, api_key=api_key),
    )


class PydanticAIRunner(Generic[T]):
    def __init__(self, *, model: Any, output_type: Any, instructions: str,
                 deps_type: type[Any] | None = None, timeout: float | None = 180.0,
                 retries: int = 2, label: str | None = None):
        try:
            from pydantic_ai import Agent  # type: ignore
        except ImportError as exc:
            raise PydanticAIUnavailable(
                "PydanticAI is not installed. Install benchmark-forge or use the deterministic MVP agents."
            ) from exc
        kwargs: dict[str, Any] = {"output_type": output_type, "instructions": instructions}
        if deps_type is not None:
            kwargs["deps_type"] = deps_type
        self.agent = Agent(model, retries=retries, **kwargs)
        self.timeout = timeout
        self.retries = retries
        self.label = label or getattr(output_type, "__name__", "pydantic_ai_call")
        self.output_type = output_type

    def run_sync(self, prompt: str, *, deps: Any = None) -> T:
        model_settings = {"timeout": self.timeout} if self.timeout is not None else None
        started = time.monotonic()
        telemetry = _CURRENT_TELEMETRY.get()
        try:
            result = _run_with_wall_clock_timeout(
                lambda: self.agent.run_sync(prompt, deps=deps, model_settings=model_settings),
                self.timeout,
            )
            output = result.output
        except Exception as exc:
            if telemetry is not None:
                telemetry.record(label=self.label, output_type=self.output_type, status="failed",
                                 duration_seconds=time.monotonic() - started,
                                 error=f"{type(exc).__name__}: {exc}")
            raise
        if telemetry is not None:
            telemetry.record(label=self.label, output_type=self.output_type, status="completed",
                             duration_seconds=time.monotonic() - started)
        return output
