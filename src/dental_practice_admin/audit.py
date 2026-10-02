"""Durable local execution evidence; credentials never belong in audit files."""
from __future__ import annotations

import inspect
import json
import os
import re
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from pydantic import BaseModel, SecretStr

from dental_practice_admin.config import Settings

CURRENT: ContextVar[Audit | None] = ContextVar("execution_audit", default=None)
P = ParamSpec("P")
R = TypeVar("R")
SENSITIVE = re.compile(r"password|secret|token|authorization|cookie|api.?key", re.I)


class Audit:
    """Append and flush each event before the operation it authorises proceeds."""

    def __init__(self, settings: Settings, identifier: str) -> None:
        self.path = settings.data_dir / "audits" / f"{identifier}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets = [v.get_secret_value() for v in vars(settings).values()
                        if isinstance(v, SecretStr) and v.get_secret_value()]
        self.secrets += [settings.firebase_key] if settings.firebase_key else []

    def clean(self, value: Any) -> Any:
        """Remove credential fields and configured secret values, including in strings."""
        if isinstance(value, BaseModel):
            return self.clean(value.model_dump(mode="json"))
        if isinstance(value, dict):
            return {str(k): "[credential]" if SENSITIVE.search(str(k)) else self.clean(v)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        if isinstance(value, str):
            for secret in sorted(self.secrets, key=len, reverse=True):
                value = value.replace(secret, "[credential]")
        return value

    def write(self, event: str, **values: Any) -> None:
        """Persist an event on local disk; IO failures propagate to the caller."""
        record = self.clean({"at": datetime.now(UTC).isoformat(), "event": event, **values})
        with self.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, default=str) + "\n")
            output.flush()
            os.fsync(output.fileno())


@contextmanager
def recording(audit: Audit) -> Iterator[None]:
    """Bind an execution to async descendants without mixing concurrent staff work."""
    token = CURRENT.set(audit)
    try:
        yield
    finally:
        CURRENT.reset(token)


def observed(operation: str) -> Callable[[Callable[P, Coroutine[Any, Any, R]]],
                                          Callable[P, Coroutine[Any, Any, R]]]:
    """Record integration calls at their shared boundary, including failures."""
    def decorate(function: Callable[P, Coroutine[Any, Any, R]]
                 ) -> Callable[P, Coroutine[Any, Any, R]]:
        @wraps(function)
        async def invoke(*args: P.args, **kwargs: P.kwargs) -> R:
            audit = CURRENT.get()
            if audit is None:
                return await function(*args, **kwargs)
            arguments = dict(inspect.signature(function).bind(*args, **kwargs).arguments)
            arguments.pop("self", None)
            arguments.pop("server", None)
            audit.write("call", operation=operation, arguments=arguments)
            try:
                result = await function(*args, **kwargs)
            except BaseException as error:
                audit.write("call_failed", operation=operation, error=type(error).__name__)
                raise
            audit.write("call_returned", operation=operation, result=result)
            return result
        return invoke
    return decorate
