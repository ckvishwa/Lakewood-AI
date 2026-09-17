"""
Domain-weighted STT accuracy (T-013 Track B, item 5).

Generic word-error-rate is the wrong metric here: a transcript can be 95%
word-accurate and still turn "no cheese" into "more cheese" — WER would
barely notice; an order would be built wrong. This measures whether the
specific word categories that actually change a pizza order survive
transcription: topping names, sizes, quantities, negations, and
half/left/right scope words. Reported per category, never one aggregate
number, matching CLAUDE.md's "track failures by category" rule for the NL
interpreter eval and applying the same discipline one layer earlier.

Run: `python -m lakewood.stt.eval` (fake provider by default — set
LAKEWOOD_STT_PROVIDER=faster_whisper for the real local model).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from .base import STTCallError, STTProvider, UnusableAudioError

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


@dataclass
class CaseResult:
    case_id: str
    transcript: str = ""
    error: str = ""
    category_hits: dict = field(default_factory=dict)  # category -> (hit, total)


def _load_manifest(manifest_path: str | None = None) -> list[dict]:
    path = manifest_path or os.path.join(FIXTURES_DIR, "manifest.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)["cases"]


def _word_present(word: str, transcript: str) -> bool:
    return re.search(rf"\b{re.escape(word.lower())}\b", transcript.lower()) is not None


def score(provider: STTProvider, manifest_path: str | None = None) -> list[CaseResult]:
    cases = _load_manifest(manifest_path)
    out = []
    for c in cases:
        audio_path = os.path.join(FIXTURES_DIR, f"{c['id']}.wav")
        r = CaseResult(case_id=c["id"])
        try:
            result = provider.transcribe(audio_path, correlation_id=c["id"])
            r.transcript = result.transcript
        except (STTCallError, UnusableAudioError) as e:
            r.error = str(e)
            out.append(r)
            continue
        for category, words in c["domain_words"].items():
            hit = sum(1 for w in words if _word_present(w, r.transcript))
            r.category_hits[category] = (hit, len(words))
        out.append(r)
    return out


def summarize(results: list[CaseResult]) -> dict[str, tuple[int, int]]:
    totals: dict[str, tuple[int, int]] = {}
    for r in results:
        for cat, (hit, total) in r.category_hits.items():
            h, t = totals.get(cat, (0, 0))
            totals[cat] = (h + hit, t + total)
    return totals


def main():
    from .faster_whisper_provider import make_stt_provider
    provider = make_stt_provider()
    results = score(provider)
    errored = [r for r in results if r.error]
    for r in results:
        status = f"ERROR: {r.error}" if r.error else r.transcript
        print(f"  {r.case_id}: {status}")
    print(f"\n{len(results) - len(errored)}/{len(results)} fixtures transcribed "
          f"without error (provider={os.environ.get('LAKEWOOD_STT_PROVIDER', 'fake')})")
    print("\n-- domain-weighted accuracy by category --")
    for cat, (hit, total) in summarize(results).items():
        pct = f"{100 * hit / total:.0f}%" if total else "n/a"
        print(f"  {cat:12s} {hit}/{total} ({pct})")


if __name__ == "__main__":
    main()
