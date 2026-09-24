"""T-053 Phase 2 Part 2 — mu-law codec + rate conversion."""

import array

from lakewood.telephony.audio_codec import (
    mulaw_to_pcm16, pcm16_to_mulaw, resample_pcm16,
)


def _pcm16(values: list[int]) -> bytes:
    return array.array("h", values).tobytes()


def test_silence_round_trips_exactly():
    silence = _pcm16([0] * 160)   # 20ms @ 8kHz, Twilio's own real frame size
    mulaw = pcm16_to_mulaw(silence)
    assert len(mulaw) == 160   # mu-law is 1 byte/sample, half the size of PCM16
    back = mulaw_to_pcm16(mulaw)
    assert back == silence


def test_round_trip_is_lossy_but_bounded():
    """mu-law is a companding codec, not lossless — real quantization error
    is expected and fine (this is what real Twilio audio looks like); it
    must stay small relative to the signal, not wildly wrong."""
    original = _pcm16([3000, -3000, 15000, -15000, 100, -100] * 20)
    mulaw = pcm16_to_mulaw(original)
    back = array.array("h", mulaw_to_pcm16(mulaw))
    orig = array.array("h", original)
    for o, b in zip(orig, back):
        # mu-law's worst-case quantization error is roughly proportional to
        # amplitude (companding) — a generous bound, not a tight tolerance.
        assert abs(o - b) <= max(200, abs(o) * 0.05 + 50)


def test_pcm16_to_mulaw_halves_byte_length():
    pcm = _pcm16(list(range(0, 3200, 4)))
    mulaw = pcm16_to_mulaw(pcm)
    assert len(mulaw) == len(pcm) // 2


def test_resample_identity_when_rates_match():
    pcm = _pcm16([1, 2, 3, 4, 5])
    assert resample_pcm16(pcm, 8000, 8000) is pcm or resample_pcm16(pcm, 8000, 8000) == pcm


def test_resample_8k_to_16k_roughly_doubles_sample_count():
    pcm = _pcm16(list(range(0, 1600, 2)))   # 400 samples @ 8kHz = 50ms
    up = resample_pcm16(pcm, 8000, 16000)
    samples_in = len(pcm) // 2
    samples_out = len(up) // 2
    assert abs(samples_out - samples_in * 2) <= 2


def test_resample_16k_to_8k_roughly_halves_sample_count():
    pcm = _pcm16(list(range(0, 3200, 2)))   # 800 samples @ 16kHz = 50ms
    down = resample_pcm16(pcm, 16000, 8000)
    samples_in = len(pcm) // 2
    samples_out = len(down) // 2
    assert abs(samples_out - samples_in // 2) <= 2


def test_resample_never_produces_empty_output_for_nonempty_input():
    pcm = _pcm16([1000, -1000, 500, -500] * 10)
    assert len(resample_pcm16(pcm, 8000, 16000)) > 0
    assert len(resample_pcm16(pcm, 22050, 8000)) > 0
