#!/usr/bin/env python3
"""Voice workload harness: scripted spoken calls against a deployed Foundry voice agent.

Plays a fixed corpus of caller utterances (synthetic TTS audio) into one or more
deployed voice agents over the managed voice WebSocket and measures, per turn:
speech-recognition accuracy (WER, entity accuracy), task outcome against the
expected decision, end of speech -> first agent audio, end of speech -> answer
audio, turn completion, call duration and barge-in handling. Audio conditions:
16 kHz wideband, 8 kHz telephony band and 8 kHz telephony band with seeded noise.

Subcommands (all live steps are explicit; nothing runs unattended):

  prepare  synthesize the corpus once with Azure text to speech (16 kHz) and
           derive the 8 kHz conditions; writes WAVs and their sha256 to --audio-dir.
  run      budget + production gates, then one call per (variant, condition,
           language) into --results-dir (raw JSONL with transcripts, local only).
  report   aggregate the results, read Voice Live token metrics from Azure Monitor
           for each variant's time window, price them with the Azure Retail Prices
           API (calculated, not billed), write specs/voice-eval-manifest.json
           (aggregates only, no transcripts) and a Markdown comparison table.

Synthetic TTS audio is not a substitute for testing with human callers, real
phones, accents and real background noise. The manifest records that boundary.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "threadlight.voice-eval/v1"
TOOL_VERSION = "0.1.0"
CAVEAT = ("Synthetic text-to-speech callers only. This is not a substitute for testing with "
          "human callers, real phones and networks, accents and real background noise.")

HUMAN_CAVEAT = ("One person's voice through a browser microphone in an ordinary room: real speech, but not "
                "a phone line, not a population of callers, accents or background conditions.")
AUDIO_SOURCES = {"synthetic-tts": CAVEAT, "human-mic": HUMAN_CAVEAT}

# Learn, Voice Live token usage and cost estimation (Azure OpenAI model family):
# ~10 tokens per second of input audio, ~20 per second of output audio.
INPUT_TOKENS_PER_S = 10
OUTPUT_TOKENS_PER_S = 20

CONDITIONS = ("wb16", "nb8", "nb8_noise")
NOISE_SNR_DB = 10.0
NOISE_SEED = 1111
TELEPHONY_CUTOFF_HZ = 3400.0

# --------------------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------------------


def _lowpass_taps(cutoff: float, rate: float, taps: int = 101) -> list[float]:
    """Hamming-windowed sinc low-pass, unity DC gain."""
    fc = cutoff / rate
    mid = (taps - 1) / 2
    h = []
    for n in range(taps):
        x = n - mid
        sinc = 2 * fc if x == 0 else math.sin(2 * math.pi * fc * x) / (math.pi * x)
        h.append(sinc * (0.54 - 0.46 * math.cos(2 * math.pi * n / (taps - 1))))
    total = sum(h)
    return [v / total for v in h]


def _clip16(v: float) -> int:
    return max(-32768, min(32767, int(round(v))))


def resample(samples: list[int], src: int, dst: int, cutoff: float | None = None) -> list[int]:
    """Polyphase rational resampling with an anti-alias/anti-image low-pass filter."""
    if src == dst and cutoff is None:
        return list(samples)
    g = math.gcd(src, dst)
    up, down = dst // g, src // g
    nyq = min(src, dst) / 2
    fc = min(cutoff or nyq * 0.9, nyq * 0.95)
    h = [v * up for v in _lowpass_taps(fc, src * up, taps=48 * up + 1)]
    delay = (len(h) - 1) // 2
    n_out = len(samples) * up // down
    out = []
    n_in = len(samples)
    for n in range(n_out):
        t = n * down + delay
        k = t % up
        acc = 0.0
        i = t // up
        while k < len(h):
            if 0 <= i < n_in:
                acc += h[k] * samples[i]
            k += up
            i -= 1
        out.append(_clip16(acc))
    return out


def to_telephony(samples: list[int], rate: int) -> list[int]:
    """Band-limit to 3.4 kHz and resample to 8 kHz, like a narrowband phone line."""
    return resample(samples, rate, 8000, cutoff=TELEPHONY_CUTOFF_HZ)


def _rms(samples: Iterable[float]) -> float:
    vals = list(samples)
    return math.sqrt(sum(v * v for v in vals) / len(vals)) if vals else 0.0


def add_noise(samples: list[int], snr_db: float, seed: int) -> list[int]:
    """Add seeded low-pass (pink-ish) noise at ``snr_db`` relative to the signal RMS."""
    rng = random.Random(seed)
    raw, prev = [], 0.0
    for _ in samples:
        prev = 0.9 * prev + 0.1 * rng.gauss(0.0, 1.0)
        raw.append(prev)
    speech = _rms(samples)
    scale = speech / (10 ** (snr_db / 20)) / (_rms(raw) or 1.0)
    return [_clip16(s + scale * n) for s, n in zip(samples, raw)]


_MULAW_BIAS, _MULAW_CLIP = 0x84, 32635


def mulaw_encode(samples: Iterable[int]) -> bytes:
    """ITU-T G.711 mu-law encoding (one byte per 16-bit sample)."""
    out = bytearray()
    for s in samples:
        sign = 0x80 if s < 0 else 0
        mag = min(-s if s < 0 else s, _MULAW_CLIP) + _MULAW_BIAS
        exponent = 7
        mask = 0x4000
        while exponent > 0 and not mag & mask:
            exponent -= 1
            mask >>= 1
        mantissa = (mag >> (exponent + 3)) & 0x0F
        out.append(~(sign | (exponent << 4) | mantissa) & 0xFF)
    return bytes(out)


def mulaw_decode(data: bytes) -> list[int]:
    out = []
    for b in data:
        b = ~b & 0xFF
        sign, exponent, mantissa = b & 0x80, (b >> 4) & 0x07, b & 0x0F
        mag = ((mantissa << 3) + _MULAW_BIAS) << exponent
        mag -= _MULAW_BIAS
        out.append(-mag if sign else mag)
    return out


def pcm16_bytes(samples: Iterable[int]) -> bytes:
    import array

    arr = array.array("h", samples)
    if sys.byteorder != "little":
        arr.byteswap()
    return arr.tobytes()


def read_wav(path: Path) -> tuple[int, list[int]]:
    import array

    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path}: need mono 16-bit PCM")
        rate, frames = w.getframerate(), w.readframes(w.getnframes())
    arr = array.array("h")
    arr.frombytes(frames)
    if sys.byteorder != "little":
        arr.byteswap()
    return rate, list(arr)


def write_wav(path: Path, rate: int, samples: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16_bytes(samples))


# --------------------------------------------------------------------------------------
# Text scoring
# --------------------------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = "".join(" " if unicodedata.category(c)[0] in "PS" else c for c in text)
    text = re.sub(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", " ", text)
    # Fold Greek tonos/diacritics so recognizer accent choices are not word errors.
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    raw = text.split()
    merged: list[str] = []
    i = 0
    while i < len(raw):
        j = i
        if raw[i].isdigit():
            while j < len(raw) and raw[j].isdigit():
                j += 1
        elif len(raw[i]) == 1 and raw[i].isalpha():
            while j < len(raw) and len(raw[j]) == 1 and raw[j].isalpha():
                j += 1
            if j - i < 2:
                j = i
        if j > i:
            merged.append("".join(raw[i:j]))
            i = j
        else:
            merged.append(raw[i])
            i += 1
    return merged


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate after normalization (case, punctuation, digit runs, spelled letters)."""
    ref, hyp = _tokens(reference), _tokens(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


def entity_found(digits: str, text: str) -> bool:
    return digits in re.sub(r"\D", "", text or "")


def classify_outcome(answer: str, keywords: dict[str, list[str]]) -> str | None:
    """Decision whose keyword appears first in the spoken answer (heuristic, transcripts kept)."""
    low = unicodedata.normalize("NFKC", answer or "").casefold()
    best: tuple[int, str] | None = None
    for decision, words in keywords.items():
        for w in words:
            pos = low.find(w.casefold())
            if pos >= 0 and (best is None or pos < best[0]):
                best = (pos, decision)
    return best[1] if best else None


# --------------------------------------------------------------------------------------
# Timeline metrics
# --------------------------------------------------------------------------------------

AUDIO_DELTA = {"response.output_audio.delta", "response.audio.delta"}
AUDIO_TRANSCRIPT_DONE = {"response.output_audio_transcript.done", "response.audio_transcript.done"}


def _rid(ev: dict[str, Any]) -> str:
    rid = ev.get("response_id")
    if isinstance(rid, str):
        return rid
    resp = ev.get("response")
    return resp.get("id", "") if isinstance(resp, dict) else ""


def _ms(seconds: float) -> int:
    return int(round(seconds * 1000))


def _b64len(delta: str) -> int:
    return len(base64.b64decode(delta)) if delta else 0


def _status_reason(ev: dict[str, Any]) -> str | None:
    details = (ev.get("response") or {}).get("status_details") or {}
    reason = details.get("reason") or (details.get("error") or {}).get("code")
    return str(reason)[:80] if reason else None


def trim_silence(samples: list[int], rate: int, threshold: int = 300, pad_ms: int = 100) -> list[int]:
    """Drop leading/trailing synthesizer silence so the last sample is the end of speech."""
    loud = [i for i, s in enumerate(samples) if abs(s) > threshold]
    if not loud:
        return samples
    pad = rate * pad_ms // 1000
    return samples[max(0, loud[0] - pad):min(len(samples), loud[-1] + pad)]


def turn_metrics(events: list[tuple[float, dict[str, Any]]], speech_end: float,
                 bytes_per_second: float) -> dict[str, Any]:
    """Per-turn measures from (client monotonic time, server event) pairs of one turn."""
    stopped = [t for t, e in events if e.get("type") == "input_audio_buffer.speech_stopped"]
    stopped = stopped[-1:]  # the stop that ended the caller's last segment
    responses = [_rid(e) for t, e in events if e.get("type") == "response.created"]
    last_rid = responses[-1] if responses else ""
    deltas = [(t, e) for t, e in events if e.get("type") in AUDIO_DELTA and t >= speech_end]
    items: list[str] = []
    for _, e in deltas:
        if _rid(e) == last_rid and e.get("item_id") not in items:
            items.append(e.get("item_id"))
    answer_item = items[-1] if items else None
    answer_first = next((t for t, e in deltas if e.get("item_id") == answer_item), None)
    done = [(t, e) for t, e in events if e.get("type") == "response.done"]
    final = next(((t, e) for t, e in reversed(done) if _rid(e) == last_rid), None)
    transcripts = {e.get("item_id"): e.get("transcript", "") for _, e in events
                   if e.get("type") in AUDIO_TRANSCRIPT_DONE}
    heard = " ".join(e.get("transcript", "").strip() for _, e in events
                     if e.get("type") == "conversation.item.input_audio_transcription.completed").strip()
    usage = [e["response"]["usage"] for _, e in done
             if isinstance(e.get("response"), dict) and e["response"].get("usage")]
    return {
        "server_speech_stopped_ms": _ms(stopped[0] - speech_end) if stopped else None,
        "ttfa_ms": _ms(deltas[0][0] - speech_end) if deltas else None,
        "answer_audio_ms": _ms(answer_first - speech_end) if answer_first is not None else None,
        "turn_complete_ms": _ms(final[0] - speech_end) if final else None,
        "hold_spoken": len(items) > 1,
        "input_transcript": heard,
        "answer_transcript": transcripts.get(answer_item, "") if answer_item else "",
        "response_status": final[1]["response"].get("status") if final else None,
        "response_status_reason": _status_reason(final[1]) if final else None,
        "output_audio_s": sum(_b64len(e.get("delta", "")) for _, e in deltas) / bytes_per_second,
        "usage": usage,
    }


def turn_settled(window: list[tuple[float, dict[str, Any]]], *, now: float, quiet_s: float) -> bool:
    """The caller's last segment got a response, it is done, and nothing arrived for ``quiet_s``."""
    last_stop, last_start = None, None
    for i, (_, e) in enumerate(window):
        if e.get("type") == "input_audio_buffer.speech_stopped":
            last_stop = i
        elif e.get("type") == "input_audio_buffer.speech_started":
            last_start = i
    if last_stop is None or (last_start is not None and last_start > last_stop):
        return False  # the caller has not finished speaking
    created = [_rid(e) for _, e in window[last_stop:] if e.get("type") == "response.created"]
    if not created:
        return False
    done = {_rid(e) for _, e in window[last_stop:] if e.get("type") == "response.done"}
    return created[-1] in done and now - window[-1][0] > quiet_s


def answer_audio_started(window: list[tuple[float, dict[str, Any]]], hold_text: str | list[str]) -> float | None:
    """Time of the first agent audio that is not a hold message (barge-in during the real answer)."""
    text: dict[str, str] = {}
    prefixes = [h.casefold()[:10] for h in ([hold_text] if isinstance(hold_text, str) else hold_text or []) if h]
    for t, e in window:
        kind = e.get("type")
        if kind in ("response.output_audio_transcript.delta", "response.audio_transcript.delta"):
            text[e.get("item_id")] = text.get(e.get("item_id"), "") + e.get("delta", "")
        elif kind in AUDIO_DELTA:
            said = text.get(e.get("item_id"), "").strip().casefold()
            if len(said) >= 8 and not any(said.startswith(p) for p in prefixes):
                return t
    return None


def barge_in_metrics(events: list[tuple[float, dict[str, Any]]], barge_start: float,
                     bytes_per_second: float) -> dict[str, Any]:
    """Caller starts speaking at ``barge_start`` while the agent is answering."""
    created = {_rid(e): t for t, e in events if e.get("type") == "response.created"}
    done = {_rid(e): (t, e) for t, e in events if e.get("type") == "response.done"}
    in_flight = [r for r, t in created.items() if t <= barge_start and (r not in done or done[r][0] > barge_start)]
    rid = in_flight[-1] if in_flight else None
    detect = next((t for t, e in events
                   if e.get("type") == "input_audio_buffer.speech_started" and t >= barge_start), None)
    after = 0
    if rid and detect is not None:
        after = sum(_b64len(e.get("delta", "")) for t, e in events
                    if e.get("type") in AUDIO_DELTA and _rid(e) == rid and t > detect)
    return {
        "detect_ms": _ms(detect - barge_start) if detect is not None else None,
        "interrupted_status": done[rid][1]["response"].get("status") if rid in done else None,
        "audio_after_detect_s": after / bytes_per_second,
    }


# --------------------------------------------------------------------------------------
# Gates, cost and manifest
# --------------------------------------------------------------------------------------


class GateError(RuntimeError):
    pass


def check_gates(plan: dict[str, Any], *, budget_ceiling_usd: float, endpoint_class: str,
                allow_production: bool) -> None:
    if not (isinstance(budget_ceiling_usd, (int, float)) and math.isfinite(budget_ceiling_usd)
            and budget_ceiling_usd > 0):
        raise GateError("budget ceiling must be a positive number")
    if endpoint_class not in ("non-production", "production"):
        raise GateError("endpoint class must be non-production or production")
    if plan["projected_usd"] > budget_ceiling_usd:
        raise GateError(f"projected budget {plan['projected_usd']:.2f} USD exceeds ceiling {budget_ceiling_usd:.2f}")
    if endpoint_class == "production" and not allow_production:
        raise GateError("production endpoint needs --allow-production")


def estimate_audio_tokens(input_audio_s: float, output_audio_s: float) -> dict[str, Any]:
    return {"audio_in": int(round(input_audio_s * INPUT_TOKENS_PER_S)),
            "audio_out": int(round(output_audio_s * OUTPUT_TOKENS_PER_S)), "basis": "estimate"}


def cost_per_call(tokens: dict[str, float], prices: dict[str, float], calls: int) -> dict[str, Any]:
    parts = {k: tokens.get(k, 0) / 1000 * prices.get(f"{k}_per_1k", 0.0)
             for k in ("audio_in", "audio_out", "text_in", "text_out")}
    total = sum(parts.values())
    return {"basis": "calculated", "usd_total": round(total, 6),
            "usd_per_call": round(total / calls, 6) if calls else None,
            "usd_by_part": {k: round(v, 6) for k, v in parts.items()}}


def price_table(meters: dict[str, str], lookup) -> dict[str, float | None]:
    """Meter-name rows (audio_in, audio_out, text_in, text_out) -> USD per 1K tokens."""
    return {f"{k}_per_1k": lookup(m) for k, m in meters.items()}


def engine_model_cost(measured: dict[str, float], prices_per_1m: dict[str, float], calls: int) -> dict[str, Any]:
    """Conversation-engine model tokens priced per 1M, input split by the measured cache match rate."""
    prompt = measured.get("ProcessedPromptTokens", 0)
    cached = prompt * min(100.0, max(0.0, measured.get("AzureOpenAIContextTokensCacheMatchRate", 0.0))) / 100
    parts = {"input": (prompt - cached) / 1e6 * prices_per_1m.get("input", 0.0),
             "cached_input": cached / 1e6 * prices_per_1m.get("cached_input", prices_per_1m.get("input", 0.0)),
             "output": measured.get("GeneratedTokens", 0) / 1e6 * prices_per_1m.get("output", 0.0)}
    total = sum(parts.values())
    return {"basis": "calculated", "usd_total": round(total, 6),
            "usd_per_call": round(total / calls, 6) if calls else None,
            "usd_by_part": {k: round(v, 6) for k, v in parts.items()}}


_FORBIDDEN_KEY = re.compile(r"(transcript|prompt|authorization|secret|password|credential|audio_b64)", re.I)
_SECRET_VALUE = re.compile(r"(Bearer\s+\S{10,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|sig=|AccountKey=)")


def _scan(obj: Any, path: str = "$") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _FORBIDDEN_KEY.search(str(k)):
                raise ValueError(f"forbidden key in manifest: {path}.{k}")
            _scan(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _scan(v, f"{path}[{i}]")
    elif isinstance(obj, str) and _SECRET_VALUE.search(obj):
        raise ValueError(f"secret-shaped value in manifest at {path}")


def build_manifest(*, rows: list[dict[str, Any]], barge_in: list[dict[str, Any]], cost: list[dict[str, Any]],
                   generated_at: str, agent_versions: dict[str, str],
                   metric_sources: dict[str, str] | None = None,
                   audio_source: str = "synthetic-tts") -> dict[str, Any]:
    if audio_source not in AUDIO_SOURCES:
        raise ValueError(f"audio_source must be one of {sorted(AUDIO_SOURCES)}")
    return {
        "schema": SCHEMA,
        "tool_version": TOOL_VERSION,
        "generated_at": generated_at,
        "audio_source": audio_source,
        "human_caller_tested": audio_source == "human-mic",
        "caveat": AUDIO_SOURCES[audio_source],
        "agent_versions": agent_versions,
        "rows": rows,
        "barge_in": barge_in,
        "cost": cost,
        "metric_sources": metric_sources or {},
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    _scan(manifest)
    if (manifest.get("schema") != SCHEMA or manifest.get("audio_source") not in AUDIO_SOURCES
            or manifest.get("human_caller_tested") is not (manifest.get("audio_source") == "human-mic")):
        raise ValueError("manifest must carry the voice-eval schema and a consistent audio-source boundary")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def load_corpus(path: Path) -> dict[str, Any]:
    corpus = json.loads(Path(path).read_text(encoding="utf-8"))
    kws = corpus.get("decision_keywords")
    if not isinstance(kws, dict) or not kws:
        raise ValueError("corpus needs decision_keywords")
    ids = set()
    for u in corpus.get("utterances", []):
        for key in ("id", "lang", "voice", "speak", "reference", "expected_decision"):
            if not u.get(key):
                raise ValueError(f"utterance {u.get('id')!r} missing {key}")
        if u["expected_decision"] not in kws:
            raise ValueError(f"utterance {u['id']}: unknown decision {u['expected_decision']}")
        if u["id"] in ids:
            raise ValueError(f"duplicate utterance id {u['id']}")
        ids.add(u["id"])
    if not ids:
        raise ValueError("corpus has no utterances")
    return corpus


# --------------------------------------------------------------------------------------
# Live helpers (Azure CLI auth, TTS, WebSocket calls, Azure Monitor, Retail Prices)
# --------------------------------------------------------------------------------------


def _az(*args: str, timeout: int = 60) -> str:
    az = shutil.which("az")
    if not az:
        raise RuntimeError("Azure CLI not found")
    return subprocess.check_output([az, *args], text=True, timeout=timeout).strip()


def _token(resource: str) -> str:
    return _az("account", "get-access-token", "--resource", resource, "--query", "accessToken", "-o", "tsv")


def synthesize(text: str, voice: str, lang: str, *, region: str, resource_id: str) -> list[int]:
    """Azure text to speech REST (Entra auth), 16 kHz mono PCM."""
    ssml = (f"<speak version='1.0' xml:lang='{lang}'><voice name='{voice}'>"
            f"{text.replace('&', '&amp;').replace('<', '&lt;')}</voice></speak>")
    req = urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1", data=ssml.encode("utf-8"),
        headers={"Authorization": f"Bearer aad#{resource_id}#{_token('https://cognitiveservices.azure.com')}",
                 "Content-Type": "application/ssml+xml",
                 "X-Microsoft-OutputFormat": "riff-16khz-16bit-mono-pcm", "User-Agent": "threadlight-voice-eval"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    with tempfile.NamedTemporaryFile(suffix=".wav") as fh:
        fh.write(data)
        fh.flush()
        rate, samples = read_wav(Path(fh.name))
    if rate != 16000:
        samples = resample(samples, rate, 16000)
    return samples


def prepare(corpus: dict[str, Any], audio_dir: Path, *, region: str, resource_id: str) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for u in corpus["utterances"]:
        wb = audio_dir / f"{u['id']}.wb16.wav"
        if not wb.exists():
            write_wav(wb, 16000, trim_silence(
                synthesize(u["speak"], u["voice"], u["lang"], region=region, resource_id=resource_id), 16000))
        rate, samples = read_wav(wb)
        nb = to_telephony(samples, rate)
        write_wav(audio_dir / f"{u['id']}.nb8.wav", 8000, nb)
        write_wav(audio_dir / f"{u['id']}.nb8_noise.wav", 8000, add_noise(nb, NOISE_SNR_DB, NOISE_SEED))
        index[u["id"]] = {c: hashlib.sha256((audio_dir / f"{u['id']}.{c}.wav").read_bytes()).hexdigest()
                          for c in CONDITIONS}
    (audio_dir / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index


def wire_audio(samples: list[int], rate: int, wire: str) -> tuple[bytes, int]:
    """Convert to the voice agent's input format; returns (bytes, bytes per second)."""
    if wire == "pcmu":
        s8 = samples if rate == 8000 else to_telephony(samples, rate)
        return mulaw_encode(s8), 8000
    if wire == "pcm24":
        return pcm16_bytes(resample(samples, rate, 24000)), 48000
    raise ValueError(f"unknown wire format {wire}")


def ws_url(project_endpoint: str, agent: str) -> str:
    base = project_endpoint.rstrip("/").replace("https://", "wss://", 1)
    return f"{base}/agents/{agent}/endpoint/protocols/voice?api-version=v1&agent_session_id={uuid.uuid4()}"


async def run_call(*, url: str, token: str, wire: str, turns: list[dict[str, Any]],
                   turn_timeout_s: float = 150.0, quiet_s: float = 2.0, hold_text: str = "") -> dict[str, Any]:
    """One call: stream silence continuously, speak each turn, record every server event."""
    import websockets

    bps = 8000 if wire == "pcmu" else 48000
    frame_bytes = bps // 50  # 20 ms
    silence = (b"\xff" if wire == "pcmu" else b"\x00\x00") * (frame_bytes // (1 if wire == "pcmu" else 2))
    events: list[tuple[float, dict[str, Any]]] = []
    queue: list[bytes] = []
    marks: dict[str, float] = {}
    pending_mark: list[str] = []
    t0 = time.monotonic()
    headers = {"Authorization": f"Bearer {token}", "Foundry-Features": "VoiceAgents=V1Preview"}
    try:
        connect = websockets.connect(url, additional_headers=headers, open_timeout=60, max_size=32 * 2 ** 20)
    except TypeError:  # websockets < 14
        connect = websockets.connect(url, extra_headers=headers, open_timeout=60, max_size=32 * 2 ** 20)
    async with connect as ws:
        stop = asyncio.Event()

        async def pump() -> None:
            next_t = time.monotonic()
            while not stop.is_set():
                if queue:
                    chunk = queue.pop(0)
                    if not queue and pending_mark:
                        marks[pending_mark.pop(0)] = time.monotonic() + 0.02
                else:
                    chunk = silence
                await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                          "audio": base64.b64encode(chunk).decode()}))
                next_t += 0.02
                await asyncio.sleep(max(0.0, next_t - time.monotonic()))

        async def recv() -> None:
            async for raw in ws:
                ev = json.loads(raw)
                events.append((time.monotonic(), ev))

        def speak(audio: bytes, mark: str) -> float:
            for i in range(0, len(audio), frame_bytes):
                queue.append(audio[i:i + frame_bytes])
            pending_mark.append(mark)
            return time.monotonic()

        async def wait_for(pred, timeout: float) -> bool:
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if pred():
                    return True
                await asyncio.sleep(0.05)
            return False

        rx = asyncio.create_task(recv())
        await wait_for(lambda: any(e.get("type") in ("session.created", "session.updated") for _, e in events), 30)
        px = asyncio.create_task(pump())
        await asyncio.sleep(1.0)
        results = []
        for n, turn in enumerate(turns):
            start_idx = len(events)
            started = speak(turn["audio"], f"end{n}")
            await wait_for(lambda: f"end{n}" in marks, len(turn["audio"]) / bps + 10)
            speech_end = marks.get(f"end{n}", time.monotonic())
            barge = None
            if turn.get("barge_in"):
                b = turn["barge_in"]
                on_answer = await wait_for(lambda: answer_audio_started(events[start_idx:], hold_text) is not None,
                                           turn_timeout_s)
                await asyncio.sleep(b["after_audio_ms"] / 1000)
                barge_start = speak(b["audio"], f"barge{n}")
                await wait_for(lambda: f"barge{n}" in marks, len(b["audio"]) / bps + 10)
                barge = {"start": barge_start, "speech_end": marks.get(f"barge{n}", time.monotonic()),
                         "on_answer": on_answer}

            ok = await wait_for(lambda: turn_settled(events[start_idx:], now=time.monotonic(), quiet_s=quiet_s),
                                turn_timeout_s)
            window = events[start_idx:]
            end_ref = barge["speech_end"] if barge else speech_end
            record = {"index": n, "utterance": turn["id"], "timed_out": not ok, "speech_s": round(speech_end - started, 3),
                      **turn_metrics(window, end_ref, bps)}
            if barge:
                record["barge_in"] = {"utterance": turn["barge_in"]["id"], "during_answer_audio": barge["on_answer"],
                                      **barge_in_metrics(window, barge["start"], bps)}
            record["errors"] = [e for _, e in window if e.get("type") == "error"]
            results.append(record)
        stop.set()
        await px
        duration = time.monotonic() - t0
        await ws.close()
        rx.cancel()
    streamed_in_s = duration  # silence is streamed continuously, like a phone line
    return {"duration_s": round(duration, 3), "input_audio_streamed_s": round(streamed_in_s, 3), "turns": results,
            "event_types": sorted({e.get("type", "") for _, e in events})}


def azure_monitor_tokens(resource_id: str, start: str, end: str) -> dict[str, Any]:
    names = ["VoiceLiveAudioInputTokens", "VoiceLiveAudioOutputTokens", "VoiceLiveCachedAudioInputTokens",
             "VoiceLiveTextInputTokens", "VoiceLiveTextOutputTokens", "VoiceLiveCachedTextInputTokens",
             "ProcessedPromptTokens", "GeneratedTokens"]
    raw = _az("monitor", "metrics", "list", "--resource", resource_id, "--metrics", *names,
              "--interval", "PT1M", "--aggregation", "Total", "--start-time", start, "--end-time", end,
              "-o", "json", timeout=120)
    out = {}
    for m in json.loads(raw).get("value", []):
        out[m["name"]["value"]] = sum(d.get("total") or 0 for ts in m.get("timeseries", []) for d in ts.get("data", []))
    raw = _az("monitor", "metrics", "list", "--resource", resource_id, "--metrics",
              "AzureOpenAIContextTokensCacheMatchRate", "--interval", "PT1M", "--aggregation", "Average",
              "--start-time", start, "--end-time", end, "-o", "json", timeout=120)
    rates = [d["average"] for m in json.loads(raw).get("value", []) for ts in m.get("timeseries", [])
             for d in ts.get("data", []) if d.get("average") is not None]
    out["AzureOpenAIContextTokensCacheMatchRate"] = statistics.mean(rates) if rates else 0.0
    return out


def retail_price(meter_name: str, region: str) -> float | None:
    flt = f"meterName eq '{meter_name}' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    url = "https://prices.azure.com/api/retail/prices?$filter=" + urllib.parse.quote(flt)
    with urllib.request.urlopen(url, timeout=30) as resp:
        items = json.load(resp).get("Items", [])
    return items[0]["retailPrice"] if items else None


# --------------------------------------------------------------------------------------
# Plan, run and report
# --------------------------------------------------------------------------------------


def build_plan(corpus: dict[str, Any], variants: list[dict[str, Any]]) -> dict[str, Any]:
    """Calls: one per (variant, condition, language) plus one barge-in call per (variant, language)."""
    langs = sorted({u["lang"] for u in corpus["utterances"]})
    calls = []
    for v in variants:
        for cond in v["conditions"]:
            for lang in langs:
                utts = [u for u in corpus["utterances"] if u["lang"] == lang and not u.get("barge_in")
                        and u.get("role") != "barge_followup" and cond in u.get("conditions", CONDITIONS)
                        and cond in v["conditions"]]
                if utts:
                    calls.append({"variant": v["name"], "agent": v["agent"], "wire": v["wire"], "condition": cond,
                                  "lang": lang, "utterances": [u["id"] for u in utts]})
        for u in corpus["utterances"]:
            if u.get("barge_in"):
                calls.append({"variant": v["name"], "agent": v["agent"], "wire": v["wire"],
                              "condition": u.get("barge_in_condition", "nb8"), "lang": u["lang"],
                              "utterances": [u["id"]], "barge_in": True})
    per_call = corpus.get("projected_usd_per_call", 0.10)
    return {"calls": calls, "projected_usd": round(len(calls) * per_call, 2)}


def run(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus)
    variants = json.loads(Path(args.variants).read_text(encoding="utf-8"))
    plan = build_plan(corpus, variants)
    try:
        check_gates(plan, budget_ceiling_usd=args.budget_ceiling_usd, endpoint_class=args.endpoint_class,
                    allow_production=args.allow_production)
    except GateError as exc:
        print(f"ABORTED before any call: {exc}", file=sys.stderr)
        return 3
    by_id = {u["id"]: u for u in corpus["utterances"]}
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / "calls.jsonl"
    windows = json.loads((results_dir / "windows.json").read_text()) if (results_dir / "windows.json").exists() else {}
    only = set(args.only or [])
    for v in variants:
        if only and v["name"] not in only:
            continue
        calls = [c for c in plan["calls"] if c["variant"] == v["name"]]
        win_start = dt.datetime.now(dt.timezone.utc)
        for c in calls:
            turns = []
            for uid in c["utterances"]:
                u = by_id[uid]
                rate, samples = read_wav(Path(args.audio_dir) / f"{uid}.{c['condition']}.wav")
                audio, _ = wire_audio(samples, rate, c["wire"])
                turn = {"id": uid, "audio": audio}
                if u.get("barge_in"):
                    b = by_id[u["barge_in"]["utterance"]]
                    brate, bsamples = read_wav(Path(args.audio_dir) / f"{b['id']}.{c['condition']}.wav")
                    turn["barge_in"] = {"id": b["id"], "after_audio_ms": u["barge_in"].get("after_audio_ms", 1500),
                                        "audio": wire_audio(bsamples, brate, c["wire"])[0]}
                turns.append(turn)
            token = _token("https://ai.azure.com")
            started = dt.datetime.now(dt.timezone.utc).isoformat()
            try:
                res = asyncio.run(run_call(url=ws_url(args.project_endpoint, c["agent"]), token=token,
                                           wire=c["wire"], turns=turns, hold_text=corpus.get("hold_text", "")))
                status = "ok"
            except Exception as exc:  # recorded, never retried silently
                res, status = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}, "error"
            rec = {**{k: v2 for k, v2 in c.items() if k != "utterances"}, "utterances": c["utterances"],
                   "started_at": started, "status": status, **res}
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"{v['name']:>10} {c['condition']:>9} {c['lang']} barge={bool(c.get('barge_in'))} "
                  f"status={status} turns={len(res.get('turns', []))} dur={res.get('duration_s')}", flush=True)
        windows[v["name"]] = {"start": win_start.isoformat(), "end": dt.datetime.now(dt.timezone.utc).isoformat()}
        (results_dir / "windows.json").write_text(json.dumps(windows, indent=2) + "\n", encoding="utf-8")
        if args.gap_s and v is not variants[-1]:
            time.sleep(args.gap_s)  # separates per-variant windows at Azure Monitor's 1-minute grain
    return 0


def _pct(values: list[float], p: float) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 1)


def score_turn(turn: dict[str, Any], u: dict[str, Any], keywords: dict[str, list[str]]) -> dict[str, Any]:
    decision = classify_outcome(turn.get("answer_transcript", ""), keywords)
    return {"wer": wer(u["reference"], turn.get("input_transcript", "")),
            "entity": entity_found(u["entity_digits"], turn.get("input_transcript", "")) if u.get("entity_digits") else None,
            "decision": decision, "correct": decision == u["expected_decision"]}


def aggregate(calls: list[dict[str, Any]], corpus: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_id = {u["id"]: u for u in corpus["utterances"]}
    kw = corpus["decision_keywords"]
    groups: dict[tuple[str, str, str], list] = {}
    barges = []
    for c in calls:
        if c.get("status") != "ok":
            groups.setdefault((c["variant"], c["condition"], c["lang"]), []).append((c, None, None))
            continue
        for t in c["turns"]:
            if c.get("barge_in"):
                b = t.get("barge_in") or {}
                follow = by_id.get(b.get("utterance", ""))
                sc = score_turn(t, follow, kw) if follow else {}
                barges.append({"variant": c["variant"], "condition": c["condition"], "lang": c["lang"],
                               "during_answer_audio": b.get("during_answer_audio"),
                               "detect_ms": b.get("detect_ms"), "interrupted_status": b.get("interrupted_status"),
                               "audio_after_detect_s": round(b.get("audio_after_detect_s", 0.0), 2),
                               "follow_up_correct": sc.get("correct"), "timed_out": t.get("timed_out")})
            else:
                groups.setdefault((c["variant"], c["condition"], c["lang"]), []).append(
                    (c, t, score_turn(t, by_id[t["utterance"]], kw)))
    rows = []
    for (variant, cond, lang), items in sorted(groups.items()):
        turns = [(t, s) for _, t, s in items if t is not None]
        ents = [s["entity"] for _, s in turns if s["entity"] is not None]
        durations = {id(c): c.get("duration_s") for c, _, _ in items if c.get("status") == "ok"}
        rows.append({
            "variant": variant, "condition": cond, "lang": lang, "turns": len(turns),
            "failed_calls": sum(1 for c, t, _ in items if t is None),
            "timed_out_turns": sum(1 for t, _ in turns if t.get("timed_out")),
            "wer_mean": round(statistics.mean(s["wer"] for _, s in turns), 3) if turns else None,
            "entity_accuracy": round(sum(ents) / len(ents), 3) if ents else None,
            "outcome_accuracy": round(sum(s["correct"] for _, s in turns) / len(turns), 3) if turns else None,
            "ttfa_ms_p50": _pct([t["ttfa_ms"] for t, _ in turns], 0.5),
            "ttfa_ms_p95": _pct([t["ttfa_ms"] for t, _ in turns], 0.95),
            "answer_audio_ms_p50": _pct([t["answer_audio_ms"] for t, _ in turns], 0.5),
            "turn_complete_ms_p50": _pct([t["turn_complete_ms"] for t, _ in turns], 0.5),
            "call_duration_s_mean": round(statistics.mean(durations.values()), 1) if durations else None,
        })
    return rows, barges


def report(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus)
    results_dir = Path(args.results_dir)
    calls = [json.loads(line) for line in (results_dir / "calls.jsonl").read_text(encoding="utf-8").splitlines() if line]
    rows, barges = aggregate(calls, corpus)
    windows = json.loads((results_dir / "windows.json").read_text())
    prices_cfg = json.loads(Path(args.prices).read_text(encoding="utf-8"))
    cost_rows = []
    for variant, win in windows.items():
        vcalls = [c for c in calls if c["variant"] == variant and c.get("status") == "ok"]
        in_s = sum(c["input_audio_streamed_s"] for c in vcalls)
        out_s = sum(t.get("output_audio_s", 0) for c in vcalls for t in c["turns"])
        est = estimate_audio_tokens(in_s, out_s)
        measured = None
        if args.account_resource_id:
            start = dt.datetime.fromisoformat(win["start"]).replace(second=0, microsecond=0)
            end = dt.datetime.fromisoformat(win["end"]) + dt.timedelta(minutes=1)
            measured = azure_monitor_tokens(args.account_resource_id, start.isoformat(), end.isoformat())
        for tier, meters in prices_cfg["voice_tiers"].items():
            prices = price_table(meters, lambda m: retail_price(m, prices_cfg["region"]))
            missing = sorted(k for k, v in prices.items() if v is None)
            prices = {k: v or 0.0 for k, v in prices.items()}
            tokens = ({"audio_in": measured.get("VoiceLiveAudioInputTokens", 0) + measured.get("VoiceLiveCachedAudioInputTokens", 0),
                       "audio_out": measured.get("VoiceLiveAudioOutputTokens", 0),
                       "text_in": measured.get("VoiceLiveTextInputTokens", 0),
                       "text_out": measured.get("VoiceLiveTextOutputTokens", 0)}
                      if measured and any(measured.get(k) for k in measured if k.startswith("VoiceLive")) else est)
            c = cost_per_call(tokens, prices, len(vcalls))
            if missing:
                c["missing_prices"] = missing
            cost_rows.append({"variant": variant, "voice_tier_assumed": tier, "calls": len(vcalls),
                              "audio_minutes_per_call": round((in_s + out_s) / 60 / max(1, len(vcalls)), 2),
                              "token_basis": "azure-monitor" if tokens is not est else "learn-estimate",
                              "voice_live_metric_tokens": (int(sum(v for k, v in measured.items() if k.startswith("VoiceLive")))
                                                           if measured else None),
                              "voice_tokens": {k: int(v) for k, v in tokens.items() if k != "basis"},
                              **c})
        if measured:
            model = prices_cfg.get("model")
            if model:
                per_1m = {k: retail_price(m, prices_cfg["region"]) or 0.0 for k, m in model.items()}
                c = engine_model_cost(measured, per_1m, len(vcalls))
                cost_rows.append({"variant": variant, "voice_tier_assumed": "engine-model", "calls": len(vcalls),
                                  "audio_minutes_per_call": None, "token_basis": "azure-monitor",
                                  "voice_tokens": {"model_in": int(measured.get("ProcessedPromptTokens", 0)),
                                                   "model_out": int(measured.get("GeneratedTokens", 0)),
                                                   "cache_match_pct": int(round(measured.get(
                                                       "AzureOpenAIContextTokensCacheMatchRate", 0)))},
                                  **c})
    agent_versions = json.loads(args.agent_versions) if args.agent_versions else {}
    manifest = build_manifest(rows=rows, barge_in=barges, cost=cost_rows,
                              generated_at=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                              agent_versions=agent_versions, audio_source=args.audio_source,
                              metric_sources={"latency": "client-side event timestamps",
                                              "tokens": "Azure Monitor Voice Live metrics on the Foundry account when available",
                                              "prices": "Azure Retail Prices API, list price, calculated not billed"})
    write_manifest(Path(args.out), manifest)
    Path(args.markdown).write_text(markdown(manifest), encoding="utf-8")
    print(Path(args.markdown).read_text(encoding="utf-8"))
    return 0


def _f(v: Any, nd: int = 0) -> str:
    if v is None:
        return "n/a"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def markdown(m: dict[str, Any]) -> str:
    lines = [f"> {m['caveat']}", "",
             "| Variant | Audio | Lang | Turns | WER | Case id heard | Outcome correct | TTFA p50 / p95 (s) | Answer audio p50 (s) | Turn done p50 (s) | Call (s) |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in m["rows"]:
        s = lambda v: "n/a" if v is None else f"{v / 1000:.1f}"  # noqa: E731
        pct = lambda v: "n/a" if v is None else f"{v:.0%}"  # noqa: E731
        lines.append(f"| {r['variant']} | {r['condition']} | {r['lang']} | {r['turns']} | {pct(r['wer_mean'])} | "
                     f"{pct(r['entity_accuracy'])} | {pct(r['outcome_accuracy'])} | {s(r['ttfa_ms_p50'])} / {s(r['ttfa_ms_p95'])} | "
                     f"{s(r['answer_audio_ms_p50'])} | {s(r['turn_complete_ms_p50'])} | {_f(r['call_duration_s_mean'])} |")
    if m["barge_in"]:
        lines += ["", "| Variant | Audio | Lang | During answer audio | Barge-in detected after (ms) | Interrupted response | Agent audio after detection (s) | Follow-up correct |",
                  "|---|---|---|---|---:|---|---:|---|"]
        for b in m["barge_in"]:
            lines.append(f"| {b['variant']} | {b['condition']} | {b['lang']} | {b.get('during_answer_audio')} | {_f(b['detect_ms'])} | "
                         f"{b['interrupted_status']} | {b['audio_after_detect_s']} | {b['follow_up_correct']} |")
    if m["cost"]:
        lines += ["", "Cost per call is **calculated** from tokens and list prices (Azure Retail Prices API), not billed.", "",
                  "| Variant | Price row | Token basis | Audio min/call | Tokens | USD per call |", "|---|---|---|---:|---|---:|"]
        for c in m["cost"]:
            toks = ", ".join(f"{k} {v}" for k, v in c["voice_tokens"].items())
            lines.append(f"| {c['variant']} | {c['voice_tier_assumed']} | {c['token_basis']} | "
                         f"{_f(c['audio_minutes_per_call'], 2)} | {toks} | {_f(c['usd_per_call'], 4)} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("prepare")
    pp.add_argument("--corpus", required=True)
    pp.add_argument("--audio-dir", required=True)
    pp.add_argument("--speech-region", required=True)
    pp.add_argument("--speech-resource-id", required=True)
    pr = sub.add_parser("run")
    pr.add_argument("--corpus", required=True)
    pr.add_argument("--variants", required=True, help="JSON list of {name, agent, wire: pcm24|pcmu, conditions}")
    pr.add_argument("--audio-dir", required=True)
    pr.add_argument("--results-dir", required=True)
    pr.add_argument("--project-endpoint", required=True)
    pr.add_argument("--budget-ceiling-usd", type=float, required=True)
    pr.add_argument("--endpoint-class", choices=["non-production", "production"], required=True)
    pr.add_argument("--allow-production", action="store_true")
    pr.add_argument("--only", nargs="*", help="run only these variant names")
    pr.add_argument("--gap-s", type=float, default=150.0)
    rp = sub.add_parser("report")
    rp.add_argument("--corpus", required=True)
    rp.add_argument("--results-dir", required=True)
    rp.add_argument("--prices", required=True)
    rp.add_argument("--account-resource-id", default="")
    rp.add_argument("--agent-versions", default="")
    rp.add_argument("--audio-source", choices=sorted(AUDIO_SOURCES), default="synthetic-tts",
                    help="human-mic for calls recorded with voice_human_caller.py")
    rp.add_argument("--out", default="specs/voice-eval-manifest.json")
    rp.add_argument("--markdown", default="specs/voice-eval-report.md")
    args = p.parse_args(argv)
    if args.cmd == "prepare":
        prepare(load_corpus(args.corpus), Path(args.audio_dir), region=args.speech_region,
                resource_id=args.speech_resource_id)
        return 0
    return run(args) if args.cmd == "run" else report(args)


if __name__ == "__main__":
    sys.exit(main())
