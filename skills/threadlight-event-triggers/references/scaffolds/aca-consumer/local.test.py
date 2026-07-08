"""Local smoke test for the ACA Consumer receiver — no Azure needed.

    python3 local.test.py
"""
import asyncio

import receiver


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

    payload = {"message_id": "sb-msg-1", "order": 42}

    first = await receiver.handle(payload, store=store, invoke=ok, dead_letter=dead_letter)
    dup = await receiver.handle(payload, store=store, invoke=ok, dead_letter=dead_letter)
    failed = await receiver.handle(
        {"message_id": "sb-msg-2"}, store=store, invoke=boom, dead_letter=dead_letter
    )

    assert first["status"] == "processed", first
    assert dup["status"] == "skipped", dup
    assert failed["status"] == "dead_lettered", failed
    assert len(dead_letters) == 1, dead_letters
    print("OK - processed / skipped / dead_lettered all correct")


if __name__ == "__main__":
    asyncio.run(main())
