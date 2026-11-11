"""Offline tests for the voice workload harness (`scripts/voice_eval.py`).

They cover the measurement maths only: audio conversion (8 kHz telephony band,
G.711 mu-law, resampling, seeded noise), WER and entity accuracy, outcome
classification, per-turn timing from a recorded event timeline, the budget and
production gates, cost arithmetic and the manifest privacy contract. Live calls
against a deployed voice agent are not exercised here.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import voice_eval as ve  # noqa: E402

CORPUS = SKILL_ROOT / "references" / "voice-eval" / "corpus.returns-triage.json"
SCHEMA = SKILL_ROOT / "references" / "voice-eval" / "voice-eval-manifest.schema.json"


def tone(rate: int, freq: float, seconds: float, amp: int = 8000) -> list[int]:
    return [int(amp * math.sin(2 * math.pi * freq * n / rate)) for n in range(int(rate * seconds))]


def rms(samples: list[int]) -> float:
    return math.sqrt(sum(s * s for s in samples) / len(samples))


# --- audio -----------------------------------------------------------------

def test_resample_lengths_and_preserves_in_band_tone():
    src = tone(16000, 440, 1.0)
    up = ve.resample(src, 16000, 24000)
    down = ve.resample(src, 16000, 8000)
    assert len(up) == 24000 and len(down) == 8000
    assert rms(up[2000:-2000]) == pytest.approx(rms(src), rel=0.05)
    assert rms(down[1000:-1000]) == pytest.approx(rms(src), rel=0.05)


def test_telephony_band_removes_content_above_3400_hz():
    src = tone(16000, 6000, 1.0)
    nb = ve.to_telephony(src, 16000)
    assert len(nb) == 8000
    assert rms(nb[1000:-1000]) < 0.05 * rms(src)


def test_mulaw_round_trip_is_close_and_one_byte_per_sample():
    src = tone(8000, 300, 0.5)
    encoded = ve.mulaw_encode(src)
    assert isinstance(encoded, bytes) and len(encoded) == len(src)
    decoded = ve.mulaw_decode(encoded)
    err = rms([a - b for a, b in zip(src, decoded)])
    assert err < 0.03 * rms(src)
    assert ve.mulaw_encode([0]) == b"\xff"


def test_noise_is_seeded_and_hits_requested_snr():
    speech = tone(8000, 300, 1.0)
    a = ve.add_noise(speech, snr_db=10, seed=7)
    b = ve.add_noise(speech, snr_db=10, seed=7)
    c = ve.add_noise(speech, snr_db=10, seed=8)
    assert a == b and a != c
    noise = [x - y for x, y in zip(a, speech)]
    snr = 20 * math.log10(rms(speech) / rms(noise))
    assert snr == pytest.approx(10, abs=0.5)


# --- text --------------------------------------------------------------------

def test_wer_merges_spelled_letters_and_split_digits():
    ref = "Triage return R M A 2026 1 0 4 4 1 0 please"
    assert ve.wer(ref, "triage return RMA-2026-104410, please.") == 0.0
    assert ve.wer(ref, "triage return RMA 2026 104410") == pytest.approx(1 / 5)
    assert ve.wer("ciao", "") == 1.0


def test_wer_handles_greek_and_italian_casefold():
    assert ve.wer("Ελέγξτε την επιστροφή", "ελέγξτε την επιστροφή") == 0.0
    assert ve.wer("Verifica il reso", "verifica IL reso!") == 0.0


def test_entity_found_uses_digit_sequence():
    assert ve.entity_found("2026104410", "RMA 2026, 104 410")
    assert not ve.entity_found("2026104410", "RMA 2026 104 411")


def test_classify_outcome_uses_earliest_decision_keyword():
    kw = {"approve": ["approve"], "deny": ["deny", "not eligible"], "escalate": ["escalat", "supervisor"]}
    assert ve.classify_outcome("I recommend we deny this; it is not eligible to approve.", kw) == "deny"
    assert ve.classify_outcome("Escalate to a supervisor.", kw) == "escalate"
    assert ve.classify_outcome("I could not find that case.", kw) is None


# --- timeline ----------------------------------------------------------------

def timeline():
    # (t seconds, event) as recorded by the client; speech ended at t=10.0
    return [
        (10.6, {"type": "input_audio_buffer.speech_stopped"}),
        (10.7, {"type": "response.created", "response": {"id": "r1"}}),
        (11.0, {"type": "conversation.item.input_audio_transcription.completed", "transcript": "triage RMA 2026 104410"}),
        (14.5, {"type": "response.output_audio.delta", "response_id": "r1", "item_id": "hold", "delta": "AAAA"}),
        (14.9, {"type": "response.output_audio_transcript.done", "response_id": "r1", "item_id": "hold", "transcript": "One moment."}),
        (19.0, {"type": "response.output_audio.delta", "response_id": "r1", "item_id": "ans", "delta": "AAAAAAAA"}),
        (21.0, {"type": "response.output_audio_transcript.done", "response_id": "r1", "item_id": "ans", "transcript": "Approve the refund."}),
        (21.2, {"type": "response.done", "response": {"id": "r1", "status": "completed",
                                                     "usage": {"input_token_details": {"audio_tokens": 40}}}}),
    ]


def test_turn_metrics_from_timeline():
    m = ve.turn_metrics(timeline(), speech_end=10.0, bytes_per_second=48000)
    assert m["ttfa_ms"] == 4500
    assert m["answer_audio_ms"] == 9000
    assert m["turn_complete_ms"] == 11200
    assert m["server_speech_stopped_ms"] == 600
    assert m["hold_spoken"] is True
    assert m["input_transcript"] == "triage RMA 2026 104410"
    assert m["answer_transcript"] == "Approve the refund."
    assert m["response_status"] == "completed"
    assert m["output_audio_s"] == pytest.approx(9 / 48000, rel=0.01)
    assert m["usage"] == [{"input_token_details": {"audio_tokens": 40}}]


def test_barge_in_metrics():
    events = [
        (5.0, {"type": "response.created", "response": {"id": "r1"}}),
        (6.0, {"type": "response.output_audio.delta", "response_id": "r1", "item_id": "a", "delta": "AAAA"}),
        (7.4, {"type": "input_audio_buffer.speech_started"}),
        (7.5, {"type": "response.output_audio.delta", "response_id": "r1", "item_id": "a", "delta": "AAAA"}),
        (7.6, {"type": "response.done", "response": {"id": "r1", "status": "cancelled"}}),
    ]
    b = ve.barge_in_metrics(events, barge_start=7.0, bytes_per_second=3)
    assert b["detect_ms"] == 400
    assert b["interrupted_status"] == "cancelled"
    assert b["audio_after_detect_s"] == pytest.approx(1.0)


# --- gates and cost ------------------------------------------------------------

def test_budget_gate_aborts_before_any_call():
    plan = {"calls": 10, "projected_usd": 3.0}
    with pytest.raises(ve.GateError, match="budget"):
        ve.check_gates(plan, budget_ceiling_usd=2.0, endpoint_class="non-production", allow_production=False)
    with pytest.raises(ve.GateError, match="production"):
        ve.check_gates(plan, budget_ceiling_usd=5.0, endpoint_class="production", allow_production=False)
    ve.check_gates(plan, budget_ceiling_usd=5.0, endpoint_class="production", allow_production=True)


def test_cost_per_call_is_labelled_calculated():
    prices = {"audio_in_per_1k": 0.0125, "audio_out_per_1k": 0.023, "text_in_per_1k": 0.0, "text_out_per_1k": 0.0}
    tokens = {"audio_in": 10000, "audio_out": 20000, "text_in": 0, "text_out": 0}
    c = ve.cost_per_call(tokens, prices, calls=4)
    assert c["basis"] == "calculated"
    assert c["usd_per_call"] == pytest.approx((10 * 0.0125 + 20 * 0.023) / 4)


def test_estimate_tokens_uses_learn_rule_of_thumb():
    est = ve.estimate_audio_tokens(input_audio_s=60, output_audio_s=30)
    assert est == {"audio_in": 600, "audio_out": 600, "basis": "estimate"}


# --- corpus and manifest -------------------------------------------------------

def test_shipped_corpus_is_valid_and_multilingual():
    corpus = ve.load_corpus(CORPUS)
    langs = {u["lang"] for u in corpus["utterances"]}
    assert {"en-US", "it-IT"} <= langs
    assert any(u.get("barge_in") for u in corpus["utterances"])
    for u in corpus["utterances"]:
        assert u["expected_decision"] in corpus["decision_keywords"]


def test_manifest_rejects_transcripts_and_keeps_synthetic_caveat(tmp_path):
    manifest = ve.build_manifest(
        rows=[{"variant": "default", "condition": "nb8", "lang": "en-US", "turns": 3, "wer_mean": 0.1,
               "entity_accuracy": 1.0, "outcome_accuracy": 1.0, "ttfa_ms_p50": 4000, "ttfa_ms_p95": 5000,
               "answer_audio_ms_p50": 9000, "turn_complete_ms_p50": 12000, "call_duration_s_mean": 60.0}],
        barge_in=[], cost=[], generated_at="2026-11-11T10:00:00Z", agent_versions={"returns-triage": "1"},
    )
    assert manifest["schema"] == "threadlight.voice-eval/v1"
    assert manifest["audio_source"] == "synthetic-tts"
    assert manifest["human_caller_tested"] is False
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(manifest, json.loads(SCHEMA.read_text()))
    out = tmp_path / "m.json"
    ve.write_manifest(out, manifest)
    assert json.loads(out.read_text())["schema"] == "threadlight.voice-eval/v1"
    bad = dict(manifest, rows=[dict(manifest["rows"][0], answer_transcript="Approve")])
    with pytest.raises(ValueError):
        ve.write_manifest(tmp_path / "bad.json", bad)


def test_turn_settles_only_after_a_response_to_the_last_caller_segment():
    cancelled = [(1.0, {"type": "input_audio_buffer.speech_stopped"}),
                 (1.1, {"type": "response.created", "response": {"id": "r1"}}),
                 (5.0, {"type": "input_audio_buffer.speech_started"}),
                 (5.1, {"type": "response.done", "response": {"id": "r1", "status": "cancelled"}})]
    # The caller is still talking (barge-in): never settled, however long the silence.
    assert not ve.turn_settled(cancelled, now=20.0, quiet_s=2.0)
    stopped = cancelled + [(8.0, {"type": "input_audio_buffer.speech_stopped"})]
    assert not ve.turn_settled(stopped, now=20.0, quiet_s=2.0)
    answered = stopped + [(8.2, {"type": "response.created", "response": {"id": "r2"}}),
                          (30.0, {"type": "response.done", "response": {"id": "r2", "status": "completed"}})]
    assert not ve.turn_settled(answered, now=31.0, quiet_s=2.0)
    assert ve.turn_settled(answered, now=32.5, quiet_s=2.0)


def test_barge_in_waits_for_answer_audio_not_the_hold_message():
    hold = "One moment, I'm checking that."
    window = [(5.0, {"type": "response.output_audio_transcript.delta", "item_id": "h", "delta": "One moment, "}),
              (5.0, {"type": "response.output_audio.delta", "item_id": "h", "delta": "AAAA"})]
    assert ve.answer_audio_started(window, hold) is None
    window += [(30.0, {"type": "response.output_audio_transcript.delta", "item_id": "a", "delta": "Deny RMA-2026"}),
               (30.1, {"type": "response.output_audio.delta", "item_id": "a", "delta": "AAAA"})]
    assert ve.answer_audio_started(window, hold) == 30.1


def test_wer_splits_letters_from_digits():
    assert ve.wer("triage return R M A 2026 1 0 4 4 1 0", "Triage return RMA2026104410.") == 0.0


def test_price_table_maps_meter_rows_to_per_1k_keys():
    lookup = {"in-meter": 0.0125, "out-meter": 0.023}.get
    assert ve.price_table({"audio_in": "in-meter", "audio_out": "out-meter", "text_in": "missing"}, lookup) == {
        "audio_in_per_1k": 0.0125, "audio_out_per_1k": 0.023, "text_in_per_1k": None}


def test_engine_model_cost_splits_cached_input_by_cache_match_rate():
    measured = {"ProcessedPromptTokens": 1_000_000, "GeneratedTokens": 10_000, "AzureOpenAIContextTokensCacheMatchRate": 90.0}
    c = ve.engine_model_cost(measured, {"input": 2.5, "cached_input": 0.25, "output": 15.0}, calls=2)
    assert c["basis"] == "calculated"
    assert c["usd_total"] == pytest.approx(0.1 * 2.5 + 0.9 * 0.25 + 0.01 * 15.0)
    assert c["usd_per_call"] == pytest.approx(c["usd_total"] / 2)
