"""Windows SAPI adapter. Uses the OS runtime; no Python TTS/model dependency."""
from __future__ import annotations
import os, subprocess, time
from .base import TTSCallError, TTSConfigError, TTSResult

class WindowsSapiTTSProvider:
    def _run(self, script):
        try: return subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True, text=True)
        except FileNotFoundError as e: raise TTSConfigError("Windows SAPI TTS requires PowerShell on Windows.") from e
        except subprocess.CalledProcessError as e: raise TTSCallError(e.stderr.strip() or "Windows SAPI failed") from e
    def synthesize(self, text, output_path):
        start=time.monotonic()
        # Pass text/path as encoded arguments so customer text is never executable PowerShell.
        import base64
        enc=lambda s: base64.b64encode(s.encode("utf-16le")).decode()
        safe_path = output_path.replace("'", "''")
        script=f"Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.SetOutputToWaveFile('{safe_path}'); $s.Speak([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{enc(text)}'))); $s.Dispose()"
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
