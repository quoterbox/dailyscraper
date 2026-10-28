from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Awaitable, Callable
from threading import Lock
from typing import Any

from playwright.sync_api import Playwright, sync_playwright

_START_LOCK = Lock()


def start_playwright() -> Playwright:
    manager = sync_playwright()
    creation_flag = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    if sys.platform != "win32" or not creation_flag:
        return _start(manager)

    # Playwright gives no way to set creationflags for its Node driver, which
    # otherwise shares the console with us and dies on our Ctrl+C before we can
    # shut down gracefully. Patch only the driver start; the lock is needed
    # because asyncio.create_subprocess_exec is process-global.
    with _START_LOCK:
        original = asyncio.create_subprocess_exec
        asyncio.create_subprocess_exec = _with_creation_flag(original, creation_flag)
        try:
            return _start(manager)
        finally:
            asyncio.create_subprocess_exec = original


def _start(manager: Any) -> Playwright:
    try:
        return manager.start()
    except BaseException:
        _cleanup_failed_start(manager)
        raise


def _with_creation_flag(
    factory: Callable[..., Awaitable[Any]], creation_flag: int
) -> Callable[..., Awaitable[Any]]:
    async def create_subprocess(*args: Any, **kwargs: Any) -> Any:
        kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | creation_flag
        return await factory(*args, **kwargs)

    return create_subprocess


def _cleanup_failed_start(manager: Any) -> None:
    # A failed start leaves an init task whose exception must be consumed
    # before the private event loop is closed, or Python reports it on exit.
    connection = getattr(manager, "_connection", None)
    init_task = getattr(connection, "_init_task", None)
    if init_task is not None and init_task.done() and not init_task.cancelled():
        try:
            init_task.exception()
        except BaseException:
            pass
    try:
        manager.__exit__(None, None, None)
    except BaseException:
        pass
