"""Local smoke test for the Service Bus Function core — no Azure needed.

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

    async def record_dead_letter(payload, exc):
        dead_letters.append((payload, exc))

    payload = {"message_id": "m1", "amount": 10}

    first = await core.handle(payload, store=store, invoke=ok, dead_letter=record_dead_letter)
    dup = await core.handle(payload, store=store, invoke=ok, dead_letter=record_dead_letter)
    failed = await core.handle(
        {"message_id": "m2"}, store=store, invoke=boom, dead_letter=record_dead_letter
    )

    assert first["status"] == "processed", first
    assert dup["status"] == "skipped", dup
    assert failed["status"] == "dead_lettered", failed
    assert len(dead_letters) == 1, dead_letters

    # Body parsing: JSON dict, non-JSON, and message_id defaulting.
    assert core.parse_body(b'{"a": 1}', "m9")["a"] == 1
    assert core.parse_body(b"not-json", "m9")["message_id"] == "m9"

    # The real dead-letter strategy re-raises (→ host abandons → native DLQ).
    try:
        await core.raise_to_platform({"message_id": "m3"}, RuntimeError("x"))
        raise AssertionError("raise_to_platform must re-raise")
    except RuntimeError:
        pass

    print("OK - processed / skipped / dead_lettered + re-raise all correct")


if __name__ == "__main__":
    asyncio.run(main())
