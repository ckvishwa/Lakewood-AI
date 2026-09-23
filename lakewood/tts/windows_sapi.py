"""Windows SAPI adapter. Uses the OS runtime; no Python TTS/model dependency."""
from __future__ import annotations
import os, subprocess, time
from .base import TTSCallError, TTSConfigError, TTSResult

# T-051: SAPI's default Rate=0 measured ~95-99 words/minute on a clean
# readback — genuinely slow (typical conversational/audiobook TTS runs
# 150-180 wpm). Real measurement across Rate -10..+10 on this machine
# (docs/STATUS.md's T-051 entry has the full table):
#   Rate=0 -> ~99 wpm   Rate=2 -> ~126 wpm   Rate=4 -> ~158 wpm
#   Rate=1 -> ~113 wpm  Rate=3 -> ~142 wpm   Rate=5 -> ~177 wpm
# DEFAULT_RATE=3 (~142 wpm) lands in normal human conversational pace —
# a real ~30% duration cut from the default, not a "read it as fast as
# possible" push. MAX_SAFE_RATE=4 (~158 wpm) is the hard ceiling: this
# audio eventually crosses an 8kHz phone line, where speech intelligibility
# degrades with rate, and neither of these numbers has been validated over
# real compressed telephony audio — conservative until it has been.
DEFAULT_RATE = 3
MAX_SAFE_RATE = 4
MIN_RATE = -10


class WindowsSapiTTSProvider:
    def __init__(self, rate: int | None = None):
        if rate is None:
            rate = int(os.environ.get("LAKEWOOD_TTS_RATE", DEFAULT_RATE))
        if not (MIN_RATE <= rate <= MAX_SAFE_RATE):
            raise TTSConfigError(
                f"rate={rate} is outside the allowed range [{MIN_RATE}, "
                f"{MAX_SAFE_RATE}] — {MAX_SAFE_RATE} is a deliberate ceiling "
                f"(T-051/ADR-015 amendment): faster speech has not been "
                f"validated over compressed 8kHz phone-line audio, so this "
                f"is not a tunable-without-limit knob.")
        self.rate = rate

    def _run(self, script):
        try: return subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True, text=True)
        except FileNotFoundError as e: raise TTSConfigError("Windows SAPI TTS requires PowerShell on Windows.") from e
        except subprocess.CalledProcessError as e: raise TTSCallError(e.stderr.strip() or "Windows SAPI failed") from e

    def _synthesize_script(self, text: str, output_path: str) -> str:
        """Split out from `synthesize` so the rate/text/path wiring is
        unit-testable as a pure string without spawning PowerShell or
        needing audio hardware (see tests/test_speech.py)."""
        import base64
        enc = base64.b64encode(text.encode("utf-16le")).decode()
        safe_path = output_path.replace("'", "''")
        return (
            "Add-Type -AssemblyName System.Speech; "
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Rate={self.rate}; "
            f"$s.SetOutputToWaveFile('{safe_path}'); "
            f"$s.Speak([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{enc}'))); "
            "$s.Dispose()"
        )

    def synthesize(self, text, output_path):
        start=time.monotonic()
        # Pass text/path as encoded arguments so customer text is never executable PowerShell.
        script = self._synthesize_script(text, output_path)
        self._run(script)
        # subprocess exit 0 only means PowerShell didn't error; SAPI can still
        # write nowhere (e.g. an unexpanded caller-side path variable lands
        # here as a literal string, so the file appears at that literal path,
        # not at output_path). A provider that can't prove the file it was
        # asked for exists has not produced audio.
        if not os.path.isfile(output_path):
            raise TTSCallError(f"windows_sapi reported success but no file exists at {output_path!r}")
        if os.path.getsize(output_path) == 0:
            raise TTSCallError(f"windows_sapi reported success but {output_path!r} is empty")
        return TTSResult(output_path, "windows_sapi", time.monotonic()-start)
    def play(self, audio_path):
        if not os.path.isfile(audio_path):
            raise TTSCallError(f"cannot play {audio_path!r}: file does not exist")
        if os.path.getsize(audio_path) == 0:
            raise TTSCallError(f"cannot play {audio_path!r}: file is empty")
        safe_path = audio_path.replace("'", "''")
        self._run(f"$p=New-Object System.Media.SoundPlayer '{safe_path}'; $p.PlaySync()")
