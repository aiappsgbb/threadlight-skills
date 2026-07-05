"""Behaviour tests for the Service Bus Function core — Shape #6."""
from _scaffold_harness import (
    DeadLetterSink,
    FakeStore,
    boom_invoke,
    load_module,
    ok_invoke,
    run,
)

mod = load_module("function-servicebus", "receiver_core.py")


def test_module_exposes_pure_core():
    assert hasattr(mod, "handle")
    assert hasattr(mod, "derive_key")


def test_fresh_message_processes():
    store, dl = FakeStore(), DeadLetterSink()
    out = run(
        mod.handle(
            {"message_id": "m1"},
            store=store,
            invoke=ok_invoke("R"),
            dead_letter=dl,
            key_fn=mod.derive_key,
        )
    )
    assert out["status"] == "processed"
    assert dl.items == []


def test_redelivered_message_skipped():
    store, dl = FakeStore(), DeadLetterSink()
    payload = {"message_id": "m1"}
    run(mod.handle(payload, store=store, invoke=ok_invoke(), dead_letter=dl, key_fn=mod.derive_key))
    out = run(
        mod.handle(payload, store=store, invoke=ok_invoke(), dead_letter=dl, key_fn=mod.derive_key)
    )
    assert out["status"] == "skipped"


def test_failure_dead_letters():
    store, dl = FakeStore(), DeadLetterSink()
    out = run(
        mod.handle(
            {"message_id": "m2"},
            store=store,
            invoke=boom_invoke(),
            dead_letter=dl,
            key_fn=mod.derive_key,
        )
    )
    assert out["status"] == "dead_lettered"
    assert len(dl.items) == 1


def test_parse_body_json_and_fallback():
    assert mod.parse_body(b'{"a": 1}', "m9")["a"] == 1
    assert mod.parse_body(b"not-json", "m9")["message_id"] == "m9"


def test_real_dead_letter_strategy_reraises():
    import pytest

    with pytest.raises(RuntimeError):
        run(mod.raise_to_platform({"message_id": "m3"}, RuntimeError("boom")))


def test_key_uses_message_id():
    assert mod.derive_key({"message_id": "abc"}) == "sb-abc"
    assert mod.derive_key({"no": "id"}).startswith("h-")
