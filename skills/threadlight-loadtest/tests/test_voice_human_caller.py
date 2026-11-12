"""Offline tests for the human-caller client (`scripts/voice_human_caller.py`).

A person speaks into a browser microphone; a localhost proxy forwards the audio
to the deployed voice agent and records server events plus the times at which
the microphone carried speech energy. These tests cover only the measurement
logic: speech energy per frame, end of the caller's speech, splitting a recorded
call into turns, and the manifest's human-caller labelling. They never open a
microphone or a network connection.
"""
from __future__ import annotations

import base64
import json
import math
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import voice_eval as ve  # noqa: E402
import voice_human_caller as hc  # noqa: E402

SCHEMA = SKILL_ROOT / "references" / "voice-eval" / "voice-eval-manifest.schema.json"
CORPUS = SKILL_ROOT / "references" / "voice-eval" / "corpus.human-caller.json"


def frame(amp: int, n: int = 480) -> str:
    samples = [int(amp * math.sin(2 * math.pi * 300 * i / 24000)) for i in range(n)]
    return base64.b64encode(ve.pcm16_bytes(samples)).decode()


def test_frame_level_separates_speech_from_room_noise():
    assert hc.frame_rms(frame(4000)) > 2000
    assert hc.frame_rms(frame(40)) < 100
    assert hc.frame_rms("") == 0.0


def test_speech_end_is_the_last_loud_frame_before_the_server_stop():
    loud = [1.0, 1.02, 1.04, 3.5, 3.52, 9.0]
    assert hc.speech_end(loud, before=4.2) == pytest.approx(3.54)  # end of the 20 ms frame
    assert hc.speech_end(loud, before=0.5) is None


def call_events():
    return [
        (3.6, {"type": "input_audio_buffer.speech_stopped"}),
        (3.7, {"type": "response.created", "response": {"id": "r1"}}),
        (4.0, {"type": "conversation.item.input_audio_transcription.completed", "transcript": "valuti il reso"}),
        (8.5, {"type": "response.output_audio.delta", "response_id": "r1", "item_id": "h", "delta": "AAAA"}),
        (30.0, {"type": "response.output_audio.delta", "response_id": "r1", "item_id": "a", "delta": "AAAA"}),
        (31.0, {"type": "response.output_audio_transcript.done", "response_id": "r1", "item_id": "a",
                "transcript": "Propongo di negare il rimborso."}),
        (31.2, {"type": "response.done", "response": {"id": "r1", "status": "completed"}}),
    ]


def test_split_one_turn_call_measures_from_the_end_of_human_speech():
    turns = hc.split_call(call_events(), loud=[1.0, 3.48], utterance="it-deny", barge_followup=None,
                          bytes_per_second=48000)
    (t,) = turns
    assert t["utterance"] == "it-deny"
    assert t["ttfa_ms"] == 5000  # 8.5 - (3.48 + 0.02)
    assert t["answer_audio_ms"] == 26500
    assert t["input_transcript"] == "valuti il reso"
    assert t["answer_transcript"] == "Propongo di negare il rimborso."


def test_split_barge_in_call_uses_first_speech_after_answer_audio():
    events = call_events()[:5] + [
        (31.5, {"type": "input_audio_buffer.speech_started"}),
        (31.6, {"type": "response.done", "response": {"id": "r1", "status": "cancelled"}}),
        (33.0, {"type": "input_audio_buffer.speech_stopped"}),
        (33.1, {"type": "response.created", "response": {"id": "r2"}}),
        (33.4, {"type": "conversation.item.input_audio_transcription.completed", "transcript": "scusi, il 418"}),
        (60.0, {"type": "response.output_audio.delta", "response_id": "r2", "item_id": "b", "delta": "AAAA"}),
        (61.0, {"type": "response.output_audio_transcript.done", "response_id": "r2", "item_id": "b",
                "transcript": "Propongo di negare il rimborso."}),
        (61.2, {"type": "response.done", "response": {"id": "r2", "status": "completed"}}),
    ]
    loud = [1.0, 3.48, 31.0, 32.9]
    (t,) = hc.split_call(events, loud=loud, utterance="it-barge", barge_followup="it-barge-followup",
                         bytes_per_second=48000)
    assert t["barge_in"]["utterance"] == "it-barge-followup"
    assert t["barge_in"]["during_answer_audio"] is True
    assert t["barge_in"]["detect_ms"] == 500  # speech_started 31.5 - first loud frame 31.0
    assert t["barge_in"]["interrupted_status"] == "cancelled"
    assert t["answer_transcript"] == "Propongo di negare il rimborso."


def test_human_corpus_is_valid_and_has_dash_and_barge_lines():
    corpus = ve.load_corpus(CORPUS)
    ids = {u["id"] for u in corpus["utterances"]}
    assert {"en-US", "it-IT"} <= {u["lang"] for u in corpus["utterances"]}
    assert any("dash" in u["speak"].lower() or "trattino" in u["speak"].lower() for u in corpus["utterances"])
    assert any(u.get("barge_in", {}).get("utterance") in ids for u in corpus["utterances"])


def test_manifest_labels_a_human_caller_run(tmp_path):
    m = ve.build_manifest(rows=[], barge_in=[], cost=[], generated_at="2026-11-12T10:00:00Z",
                          agent_versions={}, audio_source="human-mic")
    assert m["audio_source"] == "human-mic" and m["human_caller_tested"] is True
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(m, json.loads(SCHEMA.read_text()))
    ve.write_manifest(tmp_path / "m.json", m)
    with pytest.raises(ValueError):
        ve.write_manifest(tmp_path / "bad.json", dict(m, human_caller_tested=False))
