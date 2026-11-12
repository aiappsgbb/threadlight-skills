#!/usr/bin/env python3
"""Human caller for a deployed Foundry voice agent: browser microphone -> localhost proxy -> voice agent.

One command starts a local page (http://127.0.0.1:<port>) with a script of caller lines.
The person clicks "Start call", reads the line in their own voice, listens to the answer
and clicks "End call". The proxy holds the Microsoft Entra token (a browser WebSocket
cannot send an Authorization header), forwards audio and events unchanged, and records
server events plus the times at which the microphone carried speech energy. At the end of
each call it appends one record to <results-dir>/calls.jsonl in the same shape as
`voice_eval.py run`, so `voice_eval.py report --audio-source human-mic` scores WER, outcome,
latency and barge-in exactly like the synthetic runs.

Latency is measured at the proxy: "end of speech" is the end of the last microphone frame
above the speech threshold before the server's speech_stopped event. Browser capture and
playback buffering (tens of milliseconds) are not included. Use headphones: speaker echo
can trigger the agent's interruption detection.

    python3 voice_human_caller.py --project-endpoint "$AZURE_AI_PROJECT_ENDPOINT" \\
      --agent <agent>-voice --corpus ../references/voice-eval/corpus.human-caller.json \\
      --results-dir .voice-eval/human
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import json
import math
import statistics
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import voice_eval as ve  # noqa: E402

FRAME_S = 0.02
BYTES_PER_SECOND = 48000  # PCM16 mono 24 kHz, the generated wrapper's format


def frame_rms(b64: str) -> float:
    """RMS level of one base64 PCM16 little-endian frame."""
    if not b64:
        return 0.0
    import array

    arr = array.array("h")
    arr.frombytes(base64.b64decode(b64)[: len(base64.b64decode(b64)) // 2 * 2])
    if sys.byteorder != "little":
        arr.byteswap()
    return math.sqrt(sum(v * v for v in arr) / len(arr)) if len(arr) else 0.0


def speech_end(loud: list[float], *, before: float) -> float | None:
    """End time of the last loud microphone frame received at or before ``before``."""
    prior = [t for t in loud if t <= before]
    return prior[-1] + FRAME_S if prior else None


def _answer_start(events: list[tuple[float, dict[str, Any]]], hold_text: str) -> float | None:
    started = ve.answer_audio_started(events, hold_text)
    if started is not None:
        return started
    first_item: dict[str, str] = {}
    for t, e in events:  # no transcript deltas: the second audio item of a response is the answer
        if e.get("type") in ve.AUDIO_DELTA:
            rid = ve._rid(e)
            first = first_item.setdefault(rid, e.get("item_id"))
            if e.get("item_id") != first:
                return t
    return None


def split_call(events: list[tuple[float, dict[str, Any]]], *, loud: list[float], utterance: str,
               barge_followup: str | None, bytes_per_second: float = BYTES_PER_SECOND,
               hold_text: str = "") -> list[dict[str, Any]]:
    """Turn records for one recorded call (one scripted line, optionally with a barge-in)."""
    stops = [t for t, e in events if e.get("type") == "input_audio_buffer.speech_stopped"]
    if not stops:
        return []
    end = speech_end(loud, before=stops[0]) or stops[0]
    if not barge_followup:
        return [{"index": 0, "utterance": utterance, "timed_out": False, **ve.turn_metrics(events, end, bytes_per_second)}]
    answer = _answer_start(events, hold_text)
    barge_start = next((t for t in loud if answer is not None and t >= answer), None)
    if barge_start is None:
        record = {"index": 0, "utterance": utterance, "timed_out": False, **ve.turn_metrics(events, end, bytes_per_second)}
        record["barge_in"] = {"utterance": barge_followup, "during_answer_audio": False, "detect_ms": None,
                              "interrupted_status": None, "audio_after_detect_s": 0.0}
        return [record]
    barge = ve.barge_in_metrics(events, barge_start, bytes_per_second)
    later_stops = [t for t in stops if t > barge_start]
    follow_end = (speech_end(loud, before=later_stops[0]) or later_stops[0]) if later_stops else barge_start
    restart = next((i for i, (t, e) in enumerate(events)
                    if t >= barge_start and e.get("type") == "input_audio_buffer.speech_started"), len(events))
    record = {"index": 0, "utterance": utterance, "timed_out": not later_stops,
              **ve.turn_metrics(events[restart:], follow_end, bytes_per_second)}
    record["barge_in"] = {"utterance": barge_followup, "during_answer_audio": True, **barge}
    return [record]


UI_TEXT = {
    "en": {"title": "Voice test", "start": "Start", "skip": "Skip this line", "line": "Line",
           "intro": "Headphones on. Press <b>Start</b> once and allow the microphone. Read each green line aloud in your "
                    "normal voice. The agent first says it is checking, then answers; each call ends by itself. "
                    "When an <b>orange</b> line appears, read it immediately, over the agent.",
           "connecting": "Connecting...", "say": "SAY: ", "listen": "Listening to the agent...",
           "barge": "INTERRUPT NOW: ", "done": "Done, thank you. You can close this page.",
           "you": "YOU (heard): ", "agent": "AGENT: ", "saved": "saved: "},
    "it": {"title": "Test vocale", "start": "Inizia", "skip": "Salta questa riga", "line": "Riga",
           "intro": "Metti le cuffie. Premi <b>Inizia</b> una volta e consenti il microfono. Leggi ad alta voce ogni riga "
                    "verde, con la tua voce normale. L'agente prima dice che sta verificando, poi risponde; ogni chiamata "
                    "si chiude da sola. Quando compare una riga <b>arancione</b>, leggila subito, sopra l'agente.",
           "connecting": "Connessione...", "say": "DI': ", "listen": "Ascolta la risposta dell'agente...",
           "barge": "INTERROMPI ORA: ", "done": "Finito, grazie. Puoi chiudere la pagina.",
           "you": "TU (riconosciuto): ", "agent": "AGENTE: ", "saved": "salvato: "},
}

PAGE = r"""<!doctype html><html><meta charset="utf-8"><title>Voice caller</title><link rel="icon" href="data:,">
<style>body{font:18px system-ui;margin:2rem;max-width:60rem}#now{font-size:1.9rem;font-weight:700;padding:1.2rem;border-radius:10px;background:#eef;min-height:3rem}
#now.speak{background:#dff7e6}#now.barge{background:#ffe2c6;color:#7a2d00}#now.wait{background:#f3f3f3;color:#555}
button{font-size:1.2rem;padding:.6rem 1.2rem;margin-right:.6rem}#log{white-space:pre-wrap;background:#f6f6f6;padding:.8rem;font-size:14px;max-height:20rem;overflow:auto}</style>
<h1 id="title"></h1><p id="intro"></p>
<p><button id="go"></button><button id="skip"></button> <span id="prog"></span></p>
<div id="now" class="wait"></div><h3>Live</h3><div id="log"></div>
<script>
const LINES = __LINES__, HOLDS = [].concat(__HOLD__).map(h => h.toLowerCase().slice(0, 10));
const UI = __UI__;
let ws, ctx, stream, node, playAt = 0, sources = [], idx = -1, st = {};
document.getElementById('title').textContent = UI.title; document.getElementById('intro').innerHTML = UI.intro;
document.getElementById('go').textContent = UI.start; document.getElementById('skip').textContent = UI.skip;
const now = (t, c) => { const d = document.getElementById('now'); d.textContent = t; d.className = c; };
const log = (m) => { const d = document.getElementById('log'); d.textContent = m + '\n' + d.textContent; };
function b64(bytes){let s='';for(let i=0;i<bytes.length;i+=0x8000)s+=String.fromCharCode.apply(null,bytes.subarray(i,i+0x8000));return btoa(s);}
function stopPlayback(){ for (const s of sources) { try { s.stop(); } catch(e){} } sources = []; playAt = ctx.currentTime; }
function play(delta){ const raw = Uint8Array.from(atob(delta), c => c.charCodeAt(0)); const pcm = new Int16Array(raw.buffer, 0, raw.length >> 1);
  const buf = ctx.createBuffer(1, pcm.length, 24000); const ch = buf.getChannelData(0); for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
  const src = ctx.createBufferSource(); src.buffer = buf; src.connect(ctx.destination); playAt = Math.max(playAt, ctx.currentTime + 0.02);
  src.start(playAt); playAt += buf.duration; sources.push(src); src.onended = () => { sources = sources.filter(x => x !== src); }; }
async function mic(){
  ctx = new AudioContext(); stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:false,autoGainControl:true}});
  const code = "class C extends AudioWorkletProcessor{process(i){const c=i[0][0];if(c)this.port.postMessage(c.slice(0));return true}};registerProcessor('cap',C)";
  await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([code],{type:'application/javascript'})));
  const srcNode = ctx.createMediaStreamSource(stream); node = new AudioWorkletNode(ctx, 'cap'); const mute = ctx.createGain(); mute.gain.value = 0;
  srcNode.connect(node); node.connect(mute); mute.connect(ctx.destination);
  const ratio = ctx.sampleRate / 24000; let acc = [], pos = 0;
  node.port.onmessage = (ev) => { const x = ev.data; for (; pos < x.length; pos += ratio) { const i = Math.floor(pos), f = pos - i;
      const v = x[i] * (1 - f) + (i + 1 < x.length ? x[i + 1] : x[i]) * f; acc.push(Math.max(-32768, Math.min(32767, Math.round(v * 32767)))); }
    pos -= x.length; while (acc.length >= 480) { const frame = new Int16Array(acc.splice(0, 480));
      if (ws && ws.readyState === 1) ws.send(JSON.stringify({type:'input_audio_buffer.append', audio: b64(new Uint8Array(frame.buffer))})); } };
}
function finish(){ if (!ws || st.ending) return; st.ending = true; clearTimeout(st.timer); ws.send(JSON.stringify({type:'harness.end'})); }
function maybeDone(){ if (st.answers >= st.need && sources.length === 0) setTimeout(() => { if (sources.length === 0) finish(); }, 1200); else if (st.answers >= st.need) setTimeout(maybeDone, 500); }
function next(){
  idx += 1; if (idx >= LINES.length) { now(UI.done, 'wait'); document.getElementById('prog').textContent = ''; return; }
  const l = LINES[idx]; st = {answers: 0, need: l.follow ? 2 : 1, text: {}, barged: false, ending: false};
  document.getElementById('prog').textContent = `${UI.line} ${idx + 1} / ${LINES.length}`; now(UI.connecting, 'wait');
  ws = new WebSocket(`ws://${location.host}/ws?utt=${encodeURIComponent(l.id)}`);
  st.timer = setTimeout(finish, 150000);
  ws.onmessage = (m) => { const e = JSON.parse(m.data), t = e.type;
    if (t === 'harness.ready') now(UI.say + l.speak, 'speak');
    else if (t === 'response.output_audio.delta' || t === 'response.audio.delta') play(e.delta);
    else if (t === 'input_audio_buffer.speech_started') stopPlayback();
    else if (t === 'input_audio_buffer.speech_stopped' && !st.barged) now(UI.listen, 'wait');
    else if (t === 'response.output_audio_transcript.delta' || t === 'response.audio_transcript.delta') {
      const k = e.item_id; st.text[k] = (st.text[k] || '') + (e.delta || ''); const said = st.text[k].trim().toLowerCase();
      if (l.follow && !st.barged && said.length >= 8 && !HOLDS.some(h => said.startsWith(h))) { st.barged = true; now(UI.barge + l.follow, 'barge'); } }
    else if (t === 'conversation.item.input_audio_transcription.completed') log(UI.you + e.transcript);
    else if (t === 'response.output_audio_transcript.done' || t === 'response.audio_transcript.done') { log(UI.agent + e.transcript);
      const heard = (e.transcript || '').trim().toLowerCase(); if (!HOLDS.some(h => heard.startsWith(h))) { st.answers += 1; if (st.barged && st.answers < st.need) now(UI.listen, 'wait'); } }
    else if (t === 'response.done' && st.answers >= st.need) maybeDone();
    else if (t === 'error') log('ERROR: ' + JSON.stringify(e.error || e));
    else if (t === 'harness.saved') log(UI.saved + e.summary); };
  ws.onclose = () => { ws = null; clearTimeout(st.timer); setTimeout(next, 1500); };
}
document.getElementById('go').onclick = async () => { document.getElementById('go').disabled = true; await mic(); next(); };
document.getElementById('skip').onclick = () => finish();
</script></html>"""


class Caller:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.corpus = ve.load_corpus(Path(args.corpus))
        self.by_id = {u["id"]: u for u in self.corpus["utterances"]}
        self.results = Path(args.results_dir)
        self.results.mkdir(parents=True, exist_ok=True)

    def page(self) -> bytes:
        lines = [{"id": u["id"], "lang": u["lang"], "speak": u["speak"],
                  "follow": self.by_id[u["barge_in"]["utterance"]]["speak"] if u.get("barge_in") else ""}
                 for u in self.corpus["utterances"] if u.get("role") != "barge_followup"
                 and (not self.args.only or u["id"] in self.args.only)]
        return (PAGE.replace("__AGENT__", self.args.agent).replace("__LINES__", json.dumps(lines))
                .replace("__HOLD__", json.dumps(self.corpus.get("hold_text", "")))
                .replace("__UI__", json.dumps(UI_TEXT[self.args.ui_lang], ensure_ascii=False))).encode("utf-8")

    async def handle(self, browser) -> None:
        import websockets

        query = urllib.parse.parse_qs(urllib.parse.urlparse(browser.request.path).query)
        uid = (query.get("utt") or [""])[0]
        u = self.by_id.get(uid)
        if u is None:
            await browser.close(1008, "unknown line")
            return
        events: list[tuple[float, dict[str, Any]]] = []
        loud: list[float] = []
        levels: list[float] = []
        started_at = dt.datetime.now(dt.timezone.utc).isoformat()
        t0 = time.monotonic()
        token = ve._token("https://ai.azure.com")
        headers = {"Authorization": f"Bearer {token}", "Foundry-Features": "VoiceAgents=V1Preview"}
        status = "ok"
        async with websockets.connect(ve.ws_url(self.args.project_endpoint, self.args.agent),
                                      additional_headers=headers, open_timeout=60, max_size=32 * 2 ** 20) as up:
            await browser.send(json.dumps({"type": "harness.ready"}))

            async def downstream() -> None:
                async for raw in up:
                    events.append((time.monotonic(), json.loads(raw)))
                    await browser.send(raw)

            relay = asyncio.create_task(downstream())
            try:
                async for raw in browser:
                    msg = json.loads(raw)
                    if msg.get("type") == "harness.end":
                        break
                    if msg.get("type") == "input_audio_buffer.append":
                        level = frame_rms(msg.get("audio", ""))
                        levels.append(level)
                        floor = statistics.median(levels[:50]) if len(levels) >= 25 else 0.0
                        if level > max(self.args.min_level, floor * 4):
                            loud.append(time.monotonic())
                        await up.send(raw)
            except Exception as exc:  # recorded, the call is still scored
                status = f"error: {type(exc).__name__}"
            finally:
                relay.cancel()
        duration = time.monotonic() - t0
        follow = u.get("barge_in", {}).get("utterance")
        turns = split_call(events, loud=loud, utterance=uid, barge_followup=follow,
                           hold_text=self.corpus.get("hold_text", ""))
        rec = {"variant": self.args.variant, "agent": self.args.agent, "wire": "pcm24", "condition": "human-mic",
               "lang": u["lang"], "utterances": [uid], "barge_in": bool(follow), "started_at": started_at,
               "status": "ok" if status == "ok" and turns else (status if status != "ok" else "no-speech"),
               "duration_s": round(duration, 3), "input_audio_streamed_s": round(len(levels) * FRAME_S, 3),
               "turns": turns, "mic_level_floor": round(statistics.median(levels[:50]), 1) if levels else None}
        with (self.results / "calls.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        windows_path = self.results / "windows.json"
        windows = json.loads(windows_path.read_text()) if windows_path.exists() else {}
        win = windows.setdefault(self.args.variant, {"start": started_at})
        win["end"] = dt.datetime.now(dt.timezone.utc).isoformat()
        windows_path.write_text(json.dumps(windows, indent=2) + "\n", encoding="utf-8")
        t = turns[0] if turns else {}
        summary = (f"{uid}: heard '{t.get('input_transcript', '')}', first audio {t.get('ttfa_ms')} ms, "
                   f"answer audio {t.get('answer_audio_ms')} ms, status {rec['status']}")
        print(summary, flush=True)
        try:
            await browser.send(json.dumps({"type": "harness.saved", "summary": summary}))
        except Exception:
            pass


async def serve(args: argparse.Namespace) -> None:
    from websockets.asyncio.server import serve as ws_serve
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    caller = Caller(args)

    def process_request(connection, request):
        if request.path.startswith("/ws"):
            return None
        if request.path in ("/", "/index.html"):
            body = caller.page()
            return Response(200, "OK", Headers([("Content-Type", "text/html; charset=utf-8"),
                                                ("Content-Length", str(len(body)))]), body)
        return connection.respond(404, "not found\n")

    async with ws_serve(caller.handle, "127.0.0.1", args.port, process_request=process_request, max_size=2 ** 20):
        print(f"Open http://127.0.0.1:{args.port} in Edge or Chrome (headphones on). Ctrl+C to stop.", flush=True)
        await asyncio.Future()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project-endpoint", required=True)
    p.add_argument("--agent", required=True, help="deployed voice agent name, e.g. <agent>-voice")
    p.add_argument("--variant", default="human-default")
    p.add_argument("--corpus", required=True)
    p.add_argument("--results-dir", required=True)
    p.add_argument("--only", nargs="*", help="script line ids to use, in corpus order (default: all)")
    p.add_argument("--ui-lang", choices=sorted(UI_TEXT), default="en", help="language of the page text")
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--min-level", type=float, default=500.0, help="minimum RMS (PCM16) counted as speech")
    args = p.parse_args(argv)
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
