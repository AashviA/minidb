"""
eval_cache.py measures how the semantic cache performs on a labelled set
of prompts: how often it hits, how often it's wrong, and how long each
kind of call takes.

Usage:  python eval_cache.py [questions_file.json] [threshold]
"""

import json
import statistics
import sys
import time

from minidb import Database
from semantic_cache import SemanticCache, embed, SIMILARITY_THRESHOLD


def load_questions(path):
    """Questions are asked IN ORDER, so the labels depend on order.
    Each item: {"prompt": "...", "expect": "hit" or "miss"}
      miss = a new question; the cache should NOT have seen anything like it
      hit  = a rewording of an earlier question; the cache SHOULD match it
    """
    with open(path) as f:
        items = json.load(f)
    for item in items:
        if item.get("expect") not in ("hit", "miss") or not item.get("prompt"):
            raise ValueError(f"Bad question entry: {item}")
    return items


def run_eval(items, threshold):
    db = Database()                      # fresh and empty: never load an old cache file
    cache = SemanticCache(db, threshold=threshold)

    embed("warm up")                     # load the model BEFORE timing, or the first call
                                         # gets billed for loading it
    rows = []
    for item in items:
        start = time.perf_counter()      # perf_counter: highest-resolution timer for short intervals
        _, status = cache.ask(item["prompt"])
        elapsed = time.perf_counter() - start

        if status.startswith("miss"):
            got = "miss"
        else:
            got = "hit"
        rows.append({
            "prompt": item["prompt"],
            "expect": item["expect"],
            "got": got,
            "status": status,
            "seconds": elapsed,
        })
    return rows


def summarize(rows, threshold):
    hits = [r for r in rows if r["got"] == "hit"]
    misses = [r for r in rows if r["got"] == "miss"]
    good_hits = [r for r in rows if r["expect"] == "hit" and r["got"] == "hit"]
    missed_saves = [r for r in rows if r["expect"] == "hit" and r["got"] == "miss"]
    wrong_hits = [r for r in rows if r["expect"] == "miss" and r["got"] == "hit"]
    expected_hits = sum(1 for r in rows if r["expect"] == "hit")

    print(f"\nThreshold: {threshold}   Questions: {len(rows)}")
    print(f"Cache hits: {len(hits)}   Model calls (misses): {len(misses)}")
    if expected_hits:
        print(f"Correct hits:    {len(good_hits)}/{expected_hits} rewordings matched")
    print(f"Missed savings:  {len(missed_saves)}  (a reworded question the cache failed to match)")
    print(f"WRONG hits:      {len(wrong_hits)}  (cache served an answer to a different question)")

    def describe(label, group):
        if not group:
            print(f"{label}: no calls")
            return
        times = sorted(r["seconds"] for r in group)
        p95 = times[min(len(times) - 1, int(0.95 * len(times)))]
        print(f"{label}: n={len(times)}  mean={statistics.mean(times)*1000:.1f} ms  "
              f"median={statistics.median(times)*1000:.1f} ms  p95={p95*1000:.1f} ms")

    print("\nLatency")
    describe("  hits  ", hits)
    describe("  misses", misses)
    if hits and misses:
        saved = statistics.mean(r["seconds"] for r in misses) - statistics.mean(r["seconds"] for r in hits)
        print(f"  average time saved per hit: {saved*1000:.1f} ms")

    problems = missed_saves + wrong_hits
    if problems:
        print("\nCases to look at:")
        for r in problems:
            print(f"  expected {r['expect']}, got {r['got']}: {r['prompt']}  [{r['status']}]")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "eval_questions.json"
    threshold = float(sys.argv[2]) if len(sys.argv) > 2 else SIMILARITY_THRESHOLD
    summarize(run_eval(load_questions(path), threshold), threshold)
