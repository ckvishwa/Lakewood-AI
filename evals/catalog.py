"""
T-030 Part 2 — a generated, human-readable index of every eval case.

Run:  python evals/catalog.py            (writes evals/catalog.json)

Every field is DERIVED from the corpus and, where available, from recently
captured trace files (see evals/runner.py's trace capture, T-030 Part 1) —
never hand-maintained. Re-run this after any corpus change; nothing here
goes stale on its own the way a hand-kept doc does, but it also doesn't
update itself, so it's a generation step, not a live view (the viewer,
T-030 Part 3, reads this file and re-renders it without regenerating).
"""
from __future__ import annotations

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runner import _load, score  # noqa: E402
from lakewood.chat import rule_based_adapter  # noqa: E402

_CASES_GLOB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases", "*.yaml")
_TRACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "traces")


def describe_case(case: dict) -> str:
    """One plain sentence, derived — never hand-written per case."""
    tags = ", ".join(str(t) for t in case.get("tags", [])) or "untagged"
    utterances = " -> ".join(f'"{t["user"]}"' for t in case.get("turns", []))
    exp = case.get("assert_final") or {}
    exp_parts = ", ".join(f"{k}={v}" for k, v in exp.items() if v is not None)
    n_turns = len(case.get("turns", []))
    return (f"[{tags}] {n_turns} turn(s): {utterances} "
            f"-> expects {exp_parts or 'no final-state assertion (intermediate/refusal case)'}")


def _recent_llm_traces(limit: int = 3) -> list[str]:
    files = sorted(glob.glob(os.path.join(_TRACE_DIR, "*_llm.jsonl")), reverse=True)
    return files[:limit]


def _live_status_from_traces(paths: list[str]) -> dict[str, dict]:
    """case_id -> {"passed": int, "of": int, "last_detail": str|None}"""
    status: dict[str, dict] = {}
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line)
                if "_meta" in entry:
                    continue
                cid = entry["case_id"]
                st = status.setdefault(cid, {"passed": 0, "of": 0, "last_detail": None})
                st["of"] += 1
                if entry["passed"]:
                    st["passed"] += 1
                elif not entry["passed"]:
                    st["last_detail"] = entry.get("detail")
    return status


def build_catalog() -> list[dict]:
    cases = _load(_CASES_GLOB)
    rb_results = {r.case_id: r for r in score(cases, rule_based_adapter)}
    live_paths = _recent_llm_traces()
    live_status = _live_status_from_traces(live_paths)

    catalog = []
    for c in cases:
        rb = rb_results.get(c["id"])
        live = live_status.get(c["id"])
        catalog.append({
            "id": c["id"],
            "file": c.get("_source_file"),
            "tags": c.get("tags", []),
            "description": describe_case(c),
            "utterances": [t["user"] for t in c.get("turns", [])],
            "assert_final": c.get("assert_final"),
            "rule_based": {"passed": rb.passed, "detail": rb.detail} if rb else None,
            "live_recent": (
                {"passed": live["passed"], "of": live["of"], "last_detail": live["last_detail"]}
                if live else {"passed": None, "of": 0, "last_detail": "no live data captured yet"}
            ),
        })
    return catalog


def main():
    catalog = build_catalog()
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"cases": catalog, "generated_from_traces": _recent_llm_traces()},
                  f, indent=2, default=str)
    print(f"{len(catalog)} cases -> {out_path}")


if __name__ == "__main__":
    main()
