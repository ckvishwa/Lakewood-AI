"""Primitive local push-to-talk adapter; contains no order/domain logic."""
from __future__ import annotations
import os, statistics, tempfile, time, wave
from dataclasses import dataclass
from .chat import PersistentChat, make_interpreter
from .config import CONFIG
from .persistence.memory_repository import InMemorySessionRepository
from .stt.faster_whisper_provider import make_stt_provider
from .tts import make_tts_provider

class AudioConfigError(Exception): pass

class SoundDeviceMicrophone:
    def capture(self, output_path: str, seconds: float = 5.0) -> float:
        try: import sounddevice as sd
        except ImportError as e: raise AudioConfigError("Microphone capture requires optional 'sounddevice'; install it to run local voice.") from e
        rate=16000; start=time.monotonic()
        data=sd.rec(int(seconds*rate), samplerate=rate, channels=1, dtype="int16"); sd.wait()
        with wave.open(output_path, "wb") as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate); out.writeframes(data.tobytes())
        return time.monotonic()-start

@dataclass
class VoiceTurn:
    transcript: str; reply: str; capture_seconds: float; stt_seconds: float; app_seconds: float; tts_seconds: float; total_seconds: float

class LocalVoiceLoop:
    """I/O shell around the Phase-1 PersistentChat path."""
    def __init__(self, call: PersistentChat, stt, tts, microphone):
        self.call, self.stt, self.tts, self.microphone = call, stt, tts, microphone
        self.interpreter = make_interpreter()
    def turn(self, seconds=5.0) -> VoiceTurn:
        fd, path=tempfile.mkstemp(suffix=".wav"); os.close(fd)
        turn_start=time.monotonic()
        try:
            capture=self.microphone.capture(path, seconds)
            started=time.monotonic(); stt=self.stt.transcribe(path, correlation_id=self.call.call_id); stt_time=time.monotonic()-started
            started=time.monotonic(); result=self.call.run_turn(self.interpreter, stt.transcript); app=time.monotonic()-started
            out=path + ".reply.wav"; spoken=self.tts.synthesize(result.reply, out)
            # total excludes playback: PlaySync blocks for the audio's own
            # duration (talk time, not system latency), not a stage the
            # system is slow or fast at.
            total=time.monotonic()-turn_start
            self.tts.play(out)
            return VoiceTurn(stt.transcript, result.reply, capture, stt_time, app, spoken.latency_seconds, total)
        finally:
            for p in (path, path+".reply.wav"):
                try: os.unlink(p)
                except FileNotFoundError: pass

def _percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile; exact for the small N a manual hardware
    session produces (no interpolation guesswork)."""
    ordered=sorted(values)
    k=max(0, min(len(ordered)-1, int(round(p/100*len(ordered)+0.5))-1))
    return ordered[k]

def report_latency(turns: list[VoiceTurn]) -> str:
    """Per-stage median/p95 across turns — the actual Phase 2 deliverable
    per T-038, not just 'voice works'."""
    if not turns:
        return "no turns recorded"
    stages=[("capture", "capture_seconds"), ("stt", "stt_seconds"),
            ("app (interpreter+domain)", "app_seconds"), ("tts", "tts_seconds"),
            ("total", "total_seconds")]
    lines=[f"latency over {len(turns)} turn(s):"]
    for label, attr in stages:
        values=[getattr(t, attr) for t in turns]
        med=statistics.median(values); p95=_percentile(values, 95)
        lines.append(f"  {label:26s} median={med:6.3f}s  p95={p95:6.3f}s")
    return "\n".join(lines)

def main():
    call=PersistentChat.start(InMemorySessionRepository(), CONFIG.inbound_did, "VOICE-LOCAL", "+10000000000")
    loop=LocalVoiceLoop(call, make_stt_provider(), make_tts_provider(), SoundDeviceMicrophone())
    print("LAKEWOOD LOCAL VOICE (Enter records 5 seconds; type quit to exit)")
    turns: list[VoiceTurn]=[]
    while input("Press Enter > ").strip().lower() not in {"quit", "exit"}:
        turn=loop.turn(); turns.append(turn)
        print(f"STT > {turn.transcript}\nAssistant > {turn.reply}\n"
              f"  capture={turn.capture_seconds:.3f}s stt={turn.stt_seconds:.3f}s "
              f"app={turn.app_seconds:.3f}s tts={turn.tts_seconds:.3f}s total={turn.total_seconds:.3f}s\n")
    print(report_latency(turns))
if __name__ == "__main__": main()
