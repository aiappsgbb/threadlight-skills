"""Shared harness for the trigger-scaffold test suite.

The scaffold directories have hyphenated names (``aca-job-cron``) which are not
importable module names, so we load each scaffold's Python file *by path* with
importlib. The pure ``handle()`` cores import no Azure SDK, so they load and run
under a stdlib-only CI (``pip install pytest pyyaml``) with fakes injected here.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

SCAFFOLDS = Path(__file__).resolve().parents[1] / "references" / "scaffolds"


def load_module(scaffold: str, filename: str):
    """Load ``references/scaffolds/<scaffold>/<filename>`` as a module."""
    path = SCAFFOLDS / scaffold / filename
    mod_name = "scaffold_%s_%s" % (
        scaffold.replace("-", "_"),
        filename.replace(".", "_"),
    )
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader, "cannot load %s" % path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeStore:
    """In-memory stand-in for the Cosmos ``IdempotencyStore``."""

    def __init__(self):
        self.seen: set[str] = set()

    async def is_already_processed(self, key: str) -> bool:
        return key in self.seen

    async def mark_processed(self, key: str) -> None:
        self.seen.add(key)


class DeadLetterSink:
    """Records dead-lettered ``(payload, exc)`` pairs."""

    def __init__(self):
        self.items: list[tuple] = []

    async def __call__(self, payload, exc):
        self.items.append((payload, exc))


def ok_invoke(result="ok"):
    """An agent-invoke coroutine that always succeeds with ``result``."""

    async def _invoke(payload):
        return result

    return _invoke


def boom_invoke(exc=None):
    """An agent-invoke coroutine that always raises."""
    exc = exc or RuntimeError("synthetic agent failure")

    async def _invoke(payload):
        raise exc

    return _invoke


def run(coro):
    return asyncio.run(coro)
