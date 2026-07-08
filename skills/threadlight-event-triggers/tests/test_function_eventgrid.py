"""Behaviour tests for the Event Grid Function core — Shape #7."""
import pytest

from _scaffold_harness import (
    DeadLetterSink,
    FakeStore,
    boom_invoke,
    load_module,
    ok_invoke,
    run,
)

mod = load_module("function-eventgrid", "receiver_core.py")


def test_module_exposes_pure_core():
    assert hasattr(mod, "handle")
    assert hasattr(mod, "derive_key")
    assert hasattr(mod, "build_payload")


def test_fresh_event_processes():
    store, dl = FakeStore(), DeadLetterSink()
    out = run(
        mod.handle(
            mod.build_payload("evt-1", {"amount": 10}),
            store=store,
            invoke=ok_invoke("R"),
            dead_letter=dl,
            key_fn=mod.derive_key,
        )
    )
    assert out["status"] == "processed"
    assert dl.items == []


def test_duplicate_event_skipped():
    store, dl = FakeStore(), DeadLetterSink()
    payload = mod.build_payload("evt-1", {})
    run(mod.handle(payload, store=store, invoke=ok_invoke(), dead_letter=dl, key_fn=mod.derive_key))
    out = run(
        mod.handle(payload, store=store, invoke=ok_invoke(), dead_letter=dl, key_fn=mod.derive_key)
    )
    assert out["status"] == "skipped"


def test_failure_dead_letters():
    store, dl = FakeStore(), DeadLetterSink()
    out = run(
        mod.handle(
            mod.build_payload("evt-2", {}),
            store=store,
            invoke=boom_invoke(),
            dead_letter=dl,
            key_fn=mod.derive_key,
        )
    )
    assert out["status"] == "dead_lettered"
    assert len(dl.items) == 1


def test_build_payload_and_key():
    payload = mod.build_payload("e", {"k": "v"})
    assert payload == {"k": "v", "event_id": "e"}
    assert mod.derive_key(payload) == "eg-e"


def test_missing_event_id_is_rejected():
    with pytest.raises(KeyError):
        mod.derive_key({"no": "event_id"})


def test_real_dead_letter_strategy_reraises():
    with pytest.raises(RuntimeError):
        run(mod.raise_to_platform({"event_id": "e"}, RuntimeError("boom")))
