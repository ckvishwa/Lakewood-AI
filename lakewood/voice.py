"""Local voice adapter; contains no order/domain logic.

T-050: the fixed 5-second capture window (T-038 Phase 2's dominant latency
term) is gone, replaced by Silero-VAD endpointing (`lakewood.vad`, ADR-018).
TTS is pipelined sentence-by-sentence so playback of the first sentence
overlaps synthesis of the rest — see `_speak_reply` below. Neither STT nor
any LLM provider in this codebase streams; see the module docstring on
`_speak_reply` and STATUS.md's T-050 entry for exactly why, stated plainly
rather than glossed over.
"""
from __future__ import annotations
import os, queue, re, statistics, tempfile, threading, time, wave
from dataclasses import dataclass, replace
from .chat import PersistentChat, make_interpreter
from .config import CONFIG
from .persistence.memory_repository import InMemorySessionRepository
from .printer import TicketPrinter
from .stt.base import STTCallError
from .stt.faster_whisper_provider import make_stt_provider
from .tts import make_tts_provider
from .vad import Endpointer, VadConfig

class AudioConfigError(Exception): pass


def default_vad_config() -> VadConfig:
    """Env-tunable, same pattern as the STT/LLM providers' own `os.environ`
    reads (LAKEWOOD_STT_MODEL etc.) rather than `config.py`'s StoreConfig,
    which is store/business config, not provider tuning."""
    return VadConfig(
        threshold=float(os.environ.get("LAKEWOOD_VAD_THRESHOLD", "0.5")),
        min_silence_ms=int(os.environ.get("LAKEWOOD_VAD_MIN_SILENCE_MS", "700")),
        min_speech_ms=int(os.environ.get("LAKEWOOD_VAD_MIN_SPEECH_MS", "200")),
        no_speech_timeout_seconds=float(os.environ.get("LAKEWOOD_VAD_NO_SPEECH_TIMEOUT_S", "6.0")),
        max_utterance_seconds=float(os.environ.get("LAKEWOOD_VAD_MAX_UTTERANCE_S", "15.0")),
    )


class SoundDeviceMicrophone:
    """Real microphone capture, endpointed by Silero VAD instead of a fixed
    window (T-050). `seconds` (kept for interface compatibility with the
    old fixed-window Microphone contract that `FakeMic`/tests still use) is
    now a MAX safety cap, not a recording duration — real stop time is
    whenever `Endpointer` says the customer is done talking, or this cap,
    whichever comes first. See `lakewood.vad` for the state machine and
    ADR-018 for why Silero was chosen.

    Streams audio in small polls (`poll_seconds` of new audio per read) and
    re-evaluates the Silero model over the FULL buffer captured so far on
    every poll, rather than one call per single ~32ms frame. `SileroVADModel
    .__call__` re-zeroes its internal LSTM state on every call (it is built
    for one-shot batch chunking of a complete recording, not literal
    streaming) — feeding it one tiny frame at a time would mean the model
    sees each frame with no memory of what came before it, degrading
    accuracy. Re-running over the growing buffer keeps real temporal
    context inside every call, at the cost of recomputing early frames
    repeatedly — cheap for a model this small (Silero is ~1ms/second of
    audio on CPU) over utterances capped at `max_utterance_seconds`.
    """
    def __init__(self, vad_config: VadConfig | None = None, poll_seconds: float = 0.15):
        self.vad_config = vad_config or default_vad_config()
        self.poll_seconds = poll_seconds

    def capture(self, output_path: str, seconds: float = 15.0) -> float:
        try:
            import sounddevice as sd
        except ImportError as e:
            raise AudioConfigError(
                "Microphone capture requires optional 'sounddevice'; "
                "install it to run local voice.") from e
        try:
            from faster_whisper.vad import get_vad_model
        except ImportError as e:
            raise AudioConfigError(
                "VAD-based capture requires the optional 'faster-whisper' "
                "package — it ships the Silero VAD model this endpointer "
                "uses (see ADR-018). Install it: pip install faster-whisper."
            ) from e
        import numpy as np

        model = get_vad_model()
        cap = min(seconds, self.vad_config.max_utterance_seconds) if seconds else \
            self.vad_config.max_utterance_seconds
        config = replace(self.vad_config, max_utterance_seconds=cap)
        endpointer = Endpointer(config)

        rate = 16000
        frame_samples = 512  # Silero's own window size
        poll_samples = max(frame_samples,
                          (int(rate * self.poll_seconds) // frame_samples) * frame_samples)
        start = time.monotonic()
        chunks: list = []
        with sd.InputStream(samplerate=rate, channels=1, dtype="int16") as stream:
            while True:
                data, _overflowed = stream.read(poll_samples)
                chunks.append(data[:, 0].copy())
                elapsed = time.monotonic() - start
                full = np.concatenate(chunks)
                usable = full[: (len(full) // frame_samples) * frame_samples]
                prob = float(model(usable.astype(np.float32) / 32768.0)[-1]) if len(usable) else 0.0
                if endpointer.update(elapsed, prob):
                    break
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
        with wave.open(output_path, "wb") as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
            out.writeframes(audio.tobytes())
        return time.monotonic() - start

@dataclass
class VoiceTurn:
    transcript: str
    reply: str
    capture_seconds: float
    stt_seconds: float
    app_seconds: float
    tts_seconds: float           # TTS SYNTHESIS time only (sum across sentences)
    total_seconds: float         # system latency end-to-end — EXCLUDES real
                                  # playback/talk-time, same as the original
                                  # T-038 design ("PlaySync blocks for the
                                  # audio's own duration, not a stage the
                                  # system is slow or fast at")
    # T-050 additions — default 0.0 so existing positional/keyword callers
    # (tests/test_voice.py) that don't pass these keep working unchanged.
    tts_first_audio_seconds: float = 0.0
    perceived_seconds: float = 0.0
    tts_playback_seconds: float = 0.0  # informational: real talk-time, not a latency number

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

def _split_sentences(text: str) -> list[str]:
    """Simple boundary split — no NLP dependency needed for the short,
    domain-formatted replies `chat.py::_reply_for` produces. Never splits an
    empty/whitespace-only reply into anything but a single empty-safe item."""
    text = (text or "").strip()
    if not text:
        return [text]
    parts = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    return parts or [text]

class LocalVoiceLoop:
    """I/O shell around the Phase-1 PersistentChat path."""
    def __init__(self, call: PersistentChat, stt, tts, microphone):
        self.call, self.stt, self.tts, self.microphone = call, stt, tts, microphone
        self.interpreter = make_interpreter()

    def _speak_reply(self, reply: str, path: str) -> tuple[float, float]:
        """T-050 Part 3 — sentence-level TTS pipelining, the one stage in
        this system's whole pipeline that genuinely streams.

        Neither STT provider (faster-whisper, Parakeet — see
        `lakewood/stt/`) exposes partial/incremental results; both are one
        full-file-in, one-full-transcript-out call. Nor does any
        `lakewood.llm_provider` adapter request streaming from its API
        ("stream": false / omitted everywhere), and — more fundamentally —
        it would not matter if they did: `chat.py::_reply_for` and every
        readback builder in `orders.py` construct the CUSTOMER-FACING reply
        text entirely from a completed, validated tool result. The model's
        own free-text output (`ProviderResponse.text`) is never what gets
        spoken — `_finish_turn` never reads it. There is no raw model prose
        in this system's voice path to stream in the first place; adding
        SSE-stream plumbing to all four LLM providers for zero customer-
        facing latency benefit would be exactly the "impressive
        architecture" CLAUDE.md tells this project not to build. This is a
        deliberate decision, not a skipped one.

        What DOES help: the reply text (once built) is short but often 2+
        sentences ("Got it — added a large pepperoni. Anything else?"), and
        TTS synthesis has real per-call latency. Synthesizing sentence N+1
        in a background thread while sentence N plays lets audio start
        after ONLY the first sentence's synthesis time, not the whole
        reply's — the same "perceived latency" idea Part 3 asks for,
        applied to the one stage that actually supports it.

        Returns (first_audio_seconds, synthesis_total_seconds,
        synthesis_complete_seconds, playback_total_seconds, sentence_paths).

        `synthesis_complete_seconds` is when the LAST sentence finished being
        SYNTHESIZED (real system latency — "TTS-complete" in Part 5's stage
        list) — distinct from `playback_total_seconds`, when the LAST
        sentence finished being HEARD (real talk-time; can be, and usually
        is, later than synthesis-complete, since audio takes real seconds to
        play and synthesis of later sentences overlaps earlier playback).
        The caller owns cleanup of `sentence_paths`.
        """
        sentences = _split_sentences(reply)
        paths = [f"{path}.reply{i}.wav" for i in range(len(sentences))]
        ready: queue.Queue = queue.Queue()
        start = time.monotonic()

        def _synthesize_worker():
            synth_total = 0.0
            last_done = 0.0
            for sentence, p in zip(sentences, paths):
                r = self.tts.synthesize(sentence, p)
                synth_total += r.latency_seconds
                last_done = time.monotonic() - start
                ready.put((p, synth_total, last_done))
            ready.put(None)

        worker = threading.Thread(target=_synthesize_worker, daemon=True)
        worker.start()
        first_audio: float | None = None
        synth_total = synth_complete = 0.0
        while True:
            item = ready.get()
            if item is None:
                break
            p, synth_total, synth_complete = item
            if first_audio is None:
                first_audio = time.monotonic() - start
            self.tts.play(p)
        worker.join()
        playback_total = time.monotonic() - start
        first_audio = first_audio if first_audio is not None else synth_complete
        return first_audio, synth_total, synth_complete, playback_total, paths

    def turn(self, seconds: float = 15.0) -> VoiceTurn:
        """T-050 Part 4 (barge-in-adjacent safety): capture for the NEXT
        turn cannot start until this call returns, and this call does not
        return until every reply sentence has finished PLAYING (not just
        synthesizing) — `_speak_reply` only exits its loop after its queue
        sentinel, which the worker thread only sends after the last
        `synthesize` call, and the main thread's last `self.tts.play(...)`
        call is itself blocking (`WindowsSapiTTSProvider.play` uses
        `PlaySync`). Capture and playback are therefore strictly
        non-overlapping by construction — the simple, safe default this
        task calls for, not a turn-taking signal. A customer talking over
        the readback is never captured DURING it; whatever they say is
        necessarily the START of the NEXT turn's own capture, never merged
        into the confirmation response. See
        `tests/test_voice.py::test_capture_never_overlaps_playback_across_confirmation_turns`.
        """
        fd, path=tempfile.mkstemp(suffix=".wav"); os.close(fd)
        turn_start=time.monotonic()
        cleanup=[path]
        try:
            capture=self.microphone.capture(path, seconds)
            started=time.monotonic()
            try:
                stt=self.stt.transcribe(path, correlation_id=self.call.call_id)
            except STTCallError:
                # T-039 Part 3: silence, a zero-duration recording, or a
                # provider-rejected clip must degrade to a spoken apology and
                # let the call continue — this used to propagate straight out
                # of the loop and kill the whole process, which on a phone
                # line is a dropped call, not a retryable turn.
                stt_time=time.monotonic()-started
                reply="Sorry, I didn't catch that — could you say that again?"
                first_audio, synth_total, synth_complete, playback_total, reply_paths = \
                    self._speak_reply(reply, path)
                cleanup.extend(reply_paths)
                # total = system latency only: capture + stt + (time until
                # synthesis is done, i.e. audio is ready) — excludes the
                # real talk-time spent actually playing it back, same as
                # the original T-038 design's own stated reasoning.
                total = capture + stt_time + synth_complete
                return VoiceTurn(
                    "", reply, capture, stt_time, 0.0, synth_total, total,
                    tts_first_audio_seconds=first_audio,
                    perceived_seconds=stt_time + first_audio,
                    tts_playback_seconds=playback_total)
            stt_time=time.monotonic()-started
            started=time.monotonic(); result=self.call.run_turn(self.interpreter, stt.transcript); app=time.monotonic()-started
            first_audio, synth_total, synth_complete, playback_total, reply_paths = \
                self._speak_reply(result.reply, path)
            cleanup.extend(reply_paths)
            total = capture + stt_time + app + synth_complete
            return VoiceTurn(
                stt.transcript, result.reply, capture, stt_time, app, synth_total, total,
                tts_first_audio_seconds=first_audio,
                perceived_seconds=stt_time + app + first_audio,
                tts_playback_seconds=playback_total)
        finally:
            for p in cleanup:
                try: os.unlink(p)
                except FileNotFoundError: pass

def _percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile; exact for the small N a manual hardware
    session produces (no interpolation guesswork)."""
    ordered=sorted(values)
    k=max(0, min(len(ordered)-1, int(round(p/100*len(ordered)+0.5))-1))
    return ordered[k]

def report_latency(turns: list[VoiceTurn]) -> str:
    """Per-stage median/p95 across turns — the actual Phase 2/T-050
    deliverable, not just 'voice works'. `perceived` (speech-end to first
    audio) is reported separately from `total` per T-050 Part 5 — they
    genuinely differ once TTS pipelines, and the gap is itself the point."""
    if not turns:
        return "no turns recorded"
    stages=[("capture", "capture_seconds"), ("stt", "stt_seconds"),
            ("app (interpreter+domain)", "app_seconds"),
            ("tts first audio", "tts_first_audio_seconds"),
            ("tts synthesis total", "tts_seconds"),
            ("total (system latency, excl. playback)", "total_seconds"),
            ("perceived (speech-end->first audio)", "perceived_seconds"),
            ("tts playback (talk-time, NOT a latency number)", "tts_playback_seconds")]
    lines=[f"latency over {len(turns)} turn(s):"]
    for label, attr in stages:
        values=[getattr(t, attr) for t in turns]
        med=statistics.median(values); p95=_percentile(values, 95)
        lines.append(f"  {label:36s} median={med:6.3f}s  p95={p95:6.3f}s")
    return "\n".join(lines)

def main():
    # T-049: PRINTER_DRY_RUN defaults to "1" (see config.py) — a confirmed
    # order still runs the full dispatch path and prints to the in-process
    # dry-run buffer, it just never opens a real socket/device. Real
    # hardware bring-up here is opt-in via PRINTER_HOST/PRINTER_DEVICE +
    # PRINTER_DRY_RUN=0, never the other way around.
    printer = TicketPrinter(host=CONFIG.printer_host, port=CONFIG.printer_port,
                            device=CONFIG.printer_device, dry_run=CONFIG.printer_dry_run)
    call=PersistentChat.start(InMemorySessionRepository(), CONFIG.inbound_did, "VOICE-LOCAL",
                              "+10000000000", printer=printer)
    loop=LocalVoiceLoop(call, make_stt_provider(), make_tts_provider(), SoundDeviceMicrophone())
    print("LAKEWOOD LOCAL VOICE (Enter starts listening; VAD ends the turn automatically; type quit to exit)")
    turns: list[VoiceTurn]=[]
    while input("Press Enter > ").strip().lower() not in {"quit", "exit"}:
        turn=loop.turn(); turns.append(turn)
        print(f"STT > {turn.transcript}\nAssistant > {turn.reply}\n"
              f"  capture={turn.capture_seconds:.3f}s stt={turn.stt_seconds:.3f}s "
              f"app={turn.app_seconds:.3f}s tts_first={turn.tts_first_audio_seconds:.3f}s "
              f"tts_synth={turn.tts_seconds:.3f}s total={turn.total_seconds:.3f}s "
              f"perceived={turn.perceived_seconds:.3f}s "
              f"(talk-time, not counted above: {turn.tts_playback_seconds:.3f}s)\n")
    print(report_latency(turns))
if __name__ == "__main__": main()
