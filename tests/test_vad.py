"""
T-050 Part 1 — `Endpointer`, the pure VAD state machine. No audio, no model,
no numpy: every case here is a synthetic probability sequence, run entirely
offline, proving the turn-taking LOGIC (not the Silero model itself, which
this repo does not own or need to re-validate — see ADR-018).
"""

from lakewood.vad import Endpointer, VadConfig


def _run(probs_by_time: list[tuple[float, float]], config: VadConfig | None = None):
    """Feed (elapsed_seconds, prob) pairs in order; return the elapsed_seconds
    at which `update` first returned True, or None if it never did."""
    ep = Endpointer(config)
    for t, p in probs_by_time:
        if ep.update(t, p):
            return t, ep
    return None, ep


def test_clean_utterance_stops_after_min_silence():
    cfg = VadConfig(min_silence_ms=700, min_speech_ms=200)
    # speech from 0.0 to 1.0s, then silence
    seq = [(round(t * 0.05, 2), 0.9) for t in range(21)]           # 0.0..1.0s speech
    seq += [(round(1.0 + t * 0.05, 2), 0.05) for t in range(1, 30)]  # silence after
    stop_at, ep = _run(seq, cfg)
    assert stop_at is not None
    assert ep.speech_detected
    # stopped ~700ms after silence began (1.0s), not immediately and not
    # forever — comfortably inside [1.0+0.7, 1.0+0.7+0.1] given 50ms steps
    assert 1.65 <= stop_at <= 1.80


def test_mid_sentence_pause_shorter_than_threshold_does_not_end_the_turn():
    """'large... um... pepperoni' — a ~400ms filled pause must not cut the
    customer off when min_silence_ms=700."""
    cfg = VadConfig(min_silence_ms=700, min_speech_ms=200)
    seq = [(round(t * 0.05, 2), 0.9) for t in range(10)]            # speech 0.0-0.45s ("large")
    seq += [(round(0.45 + t * 0.05, 2), 0.05) for t in range(1, 9)]  # ~400ms pause ("um")
    seq += [(round(0.85 + t * 0.05, 2), 0.9) for t in range(20)]     # speech resumes ("pepperoni")
    seq += [(round(1.85 + t * 0.05, 2), 0.05) for t in range(1, 20)]  # real silence after
    stop_at, ep = _run(seq, cfg)
    assert stop_at is not None
    assert ep.speech_detected
    # must have stopped AFTER speech resumed (>1.85s), never during the
    # short pause (which would be well under 1.85s)
    assert stop_at > 1.85


def test_background_noise_blip_does_not_start_a_turn():
    """A single loud clatter below min_speech_ms must not count as 'the
    customer started talking' — otherwise a kitchen noise starts an
    utterance that then times out empty instead of just being ignored."""
    cfg = VadConfig(min_silence_ms=700, min_speech_ms=200, no_speech_timeout_seconds=2.0)
    seq = [(0.0, 0.05), (0.05, 0.9), (0.10, 0.05)]  # one 50ms blip, way under 200ms
    seq += [(round(0.10 + t * 0.05, 2), 0.05) for t in range(1, 40)]
    stop_at, ep = _run(seq, cfg)
    assert stop_at is not None
    assert not ep.speech_detected  # never counted as real speech
    assert stop_at == 2.0  # stopped via no_speech_timeout, not min_silence


def test_silent_customer_times_out_without_hanging_forever():
    cfg = VadConfig(no_speech_timeout_seconds=6.0, max_utterance_seconds=15.0)
    seq = [(round(t * 0.1, 2), 0.02) for t in range(80)]  # 0..~7.9s pure silence
    stop_at, ep = _run(seq, cfg)
    assert stop_at is not None
    assert not ep.speech_detected
    assert stop_at == 6.0


def test_continuous_speech_stops_at_max_utterance_safety_cap():
    """Background noise that keeps tripping the threshold, or a customer who
    genuinely never pauses, must not hang the call indefinitely."""
    cfg = VadConfig(min_silence_ms=700, min_speech_ms=200, max_utterance_seconds=15.0)
    seq = [(round(t * 0.1, 2), 0.9) for t in range(200)]  # 0..~19.9s continuous "speech"
    stop_at, ep = _run(seq, cfg)
    assert stop_at is not None
    assert ep.speech_detected
    assert stop_at == 15.0


def test_speech_started_flag_distinguishes_timeout_from_real_endpoint():
    cfg = VadConfig(no_speech_timeout_seconds=1.0)
    ep = Endpointer(cfg)
    assert ep.speech_detected is False
    stopped = ep.update(1.0, 0.02)
    assert stopped
    assert ep.speech_detected is False  # a real no-input timeout, not an endpoint


def test_fresh_endpointer_per_turn_has_no_leftover_state():
    cfg = VadConfig(min_silence_ms=100, min_speech_ms=50, no_speech_timeout_seconds=5.0)
    ep1 = Endpointer(cfg)
    ep1.update(0.0, 0.9)
    ep1.update(0.06, 0.9)  # speech_started True on ep1
    assert ep1.speech_detected

    ep2 = Endpointer(cfg)  # a brand-new turn must not inherit ep1's state
    assert ep2.speech_detected is False
