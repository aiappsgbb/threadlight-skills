"""Local smoke test for the HTTP webhook Function core — no Azure needed.

Tests the pure ``receiver_core.handle()`` (function_app.py is the thin Functions
wrapper and is syntax-checked, not imported, in CI).

    python3 local.test.py
"""
import asyncio

import receiver_core as core


class FakeStore:
    def __init__(self):
        self.seen = set()

    async def is_already_processed(self, key):
        return key in self.seen

    async def mark_processed(self, key):
        self.seen.add(key)


async def main():
    store = FakeStore()
    dead_letters = []

    async def ok(payload):
        return {"ok": True}

    async def boom(payload):
        raise RuntimeError("synthetic agent failure")

    async def dead_letter(payload, exc):
        dead_letters.append((payload, exc))

    payload = {"request_id": "req-1", "amount": 10}

    first = await core.handle(payload, store=store, invoke=ok, dead_letter=dead_letter)
    dup = await core.handle(payload, store=store, invoke=ok, dead_letter=dead_letter)
    failed = await core.handle(
        {"request_id": "req-2"}, store=store, invoke=boom, dead_letter=dead_letter
    )

    assert first["status"] == "processed", first
    assert dup["status"] == "skipped", dup
    assert failed["status"] == "dead_lettered", failed
    assert len(dead_letters) == 1, dead_letters

    # X-Request-Id is required for idempotency.
    try:
        core.derive_key({"no": "request_id"})
        raise AssertionError("expected KeyError for missing request id")
    except KeyError:
        pass

    print("OK - processed / skipped / dead_lettered + required-header all correct")


if __name__ == "__main__":
    asyncio.run(main())
