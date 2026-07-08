"""Behaviour tests for the ACA Job (manual) receiver scaffold — Shape #2."""
from _scaffold_harness import (
    DeadLetterSink,
    FakeStore,
    boom_invoke,
    load_module,
    ok_invoke,
    run,
)

mod = load_module("aca-job-manual", "receiver.py")


def test_module_exposes_pure_core():
    assert hasattr(mod, "handle")
    assert hasattr(mod, "derive_key")


def test_fresh_request_processes():
    store, dl = FakeStore(), DeadLetterSink()
    out = run(
        mod.handle(
            {"request_id": "req-1"},
            store=store,
            invoke=ok_invoke("R"),
            dead_letter=dl,
            key_fn=mod.derive_key,
        )
    )
    assert out["status"] == "processed"
    assert dl.items == []


def test_duplicate_request_skipped():
    store, dl = FakeStore(), DeadLetterSink()
    payload = {"request_id": "req-1"}
    run(mod.handle(payload, store=store, invoke=ok_invoke(), dead_letter=dl, key_fn=mod.derive_key))
    out = run(
        mod.handle(payload, store=store, invoke=ok_invoke(), dead_letter=dl, key_fn=mod.derive_key)
    )
    assert out["status"] == "skipped"


def test_failure_dead_letters():
    store, dl = FakeStore(), DeadLetterSink()
    out = run(
        mod.handle(
            {"request_id": "req-2"},
            store=store,
            invoke=boom_invoke(),
            dead_letter=dl,
            key_fn=mod.derive_key,
        )
    )
    assert out["status"] == "dead_lettered"
    assert len(dl.items) == 1


def test_key_uses_request_id_and_item_id():
    assert mod.derive_key({"request_id": "r"}) == "manual-r"
    assert mod.derive_key({"request_id": "r", "id": "i"}) == "manual-r-i"
    # No request id -> stable hash fallback (starts with h-).
    assert mod.derive_key({"foo": "bar"}).startswith("h-")
