"""Local smoke test for the ACA cron receiver — no Azure needed.

Runs the pure ``handle()`` core against an in-memory idempotency store and a
fake agent invoke, exercising the three outcomes the receiver must guarantee:
fresh -> processed, duplicate -> skipped, agent failure -> dead-lettered.

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

    payload = {"run_date": "2026-07-03", "id": "case-42"}

    first = await receiver.handle(payload, store=store, invoke=ok, dead_letter=dead_letter)
    dup = await receiver.handle(payload, store=store, invoke=ok, dead_letter=dead_letter)
    failed = await receiver.handle(
        {"run_date": "2026-07-03", "id": "case-99"},
        store=store,
        invoke=boom,
        dead_letter=dead_letter,
    )

    assert first["status"] == "processed", first
    assert dup["status"] == "skipped", dup
    assert failed["status"] == "dead_lettered", failed
    assert len(dead_letters) == 1, dead_letters
    print("OK - processed / skipped / dead_lettered all correct")


if __name__ == "__main__":
    asyncio.run(main())
