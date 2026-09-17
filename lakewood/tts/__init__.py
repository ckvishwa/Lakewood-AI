from __future__ import annotations
import os
from .base import TTSConfigError
def make_tts_provider():
    name=os.environ.get("LAKEWOOD_TTS_PROVIDER", "windows_sapi")
    if name == "fake":
        from .fake import FakeTTSProvider; return FakeTTSProvider()
    if name == "windows_sapi":
        from .windows_sapi import WindowsSapiTTSProvider; return WindowsSapiTTSProvider()
    raise TTSConfigError(f"Unknown LAKEWOOD_TTS_PROVIDER={name!r}; choose 'windows_sapi' or 'fake'.")
