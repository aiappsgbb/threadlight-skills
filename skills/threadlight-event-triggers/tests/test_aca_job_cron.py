"""Behaviour tests for the ACA Job (cron) receiver scaffold — Shape #1.

Covers the SKILL Step 7 validate checklist (offline, no Azure):
  - receiver scaffold compiles / imports
  - idempotency check fires on a duplicate key
  - dead-letter rule fires on a simulated failure
  - agent invocation succeeds with a synthetic payload
"""
from _scaffold_harness import (
    DeadLetterSink,
    FakeStore,
    boom_invoke,
    load_module,
    ok_invoke,
    run,
)

mod = load_module("aca-job-cron", "receiver.py")


def test_module_exposes_pure_core():
    assert hasattr(mod, "handle")
    assert hasattr(mod, "derive_key")


def test_fresh_key_processes():
    store, dl = FakeStore(), DeadLetterSink()
    out = run(
        mod.handle(
            {"run_date": "2026-07-03"},
            store=store,
            invoke=ok_invoke("RESULT"),
            dead_letter=dl,
            key_fn=mod.derive_key,
        )
    )
    assert out["status"] == "processed"
    assert out["result"] == "RESULT"
    assert dl.items == []


def test_duplicate_key_skipped():
    store, dl = FakeStore(), DeadLetterSink()
    payload = {"run_date": "2026-07-03"}
    run(mod.handle(payload, store=store, invoke=ok_invoke(), dead_letter=dl, key_fn=mod.derive_key))
    out = run(
        mod.handle(payload, store=store, invoke=ok_invoke(), dead_letter=dl, key_fn=mod.derive_key)
    )
    assert out["status"] == "skipped"
    assert out["reason"] == "duplicate"


def test_failure_dead_letters_and_does_not_mark_processed():
    store, dl = FakeStore(), DeadLetterSink()
    payload = {"run_date": "2026-07-03", "id": "case-9"}
    out = run(
        mod.handle(payload, store=store, invoke=boom_invoke(), dead_letter=dl, key_fn=mod.derive_key)
    )
    assert out["status"] == "dead_lettered"
    assert len(dl.items) == 1
    # A failed item must remain retryable — not marked processed.
    assert not run(store.is_already_processed(mod.derive_key(payload)))


def test_key_is_run_date_scoped():
    assert mod.derive_key({"run_date": "2026-07-03"}) == "cron-2026-07-03"
    assert mod.derive_key({"run_date": "2026-07-03", "id": "x"}) == "cron-2026-07-03-x"
