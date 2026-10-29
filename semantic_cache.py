"""
semantic_cache.py is a cache that reuses answers for *similar* prompts, using a local sentence-embedding model plus MiniDB.

Needs the extra packages in requirements-demos.txt. The first run downloads
the embedding model (roughly 90 MB) and keeps a copy on disk after that.
"""

import json
import time
from typing import List, Optional, Tuple

import numpy as np

from minidb import Database
from llm_cache import call_llm, hash_prompt

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
CACHE_FILE = "semantic_cache.json"
CACHE_TTL = 24 * 60 * 60        # keep entries for 24 hours
SIMILARITY_THRESHOLD = 0.85     
KEY_PREFIX = "sem:"             # marks keys that belong to this cache

_model = None


def get_model():
    """Load the embedding model the first time it's needed, then reuse it.

    The import sits inside the function so that merely importing this file
    stays fast, and so a missing package only fails when the model is
    actually used.
    """
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed(text: str) -> np.ndarray:
    """Turn text into a vector (384 numbers for this model)."""
    return get_model().encode(text)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """1.0 = pointing the same way (very similar), 0.0 = unrelated."""
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


class SemanticCache:
    def __init__(self, db: Database,
                 threshold: float = SIMILARITY_THRESHOLD,
                 ttl: float = CACHE_TTL) -> None:
        self.db = db
        self.threshold = threshold
        self.ttl = ttl

    @staticmethod
    def _parse(raw) -> Optional[dict]:
        """Turn a stored JSON string back into a dict, or None if it isn't
        a usable entry from this model (wrong type, corrupted, old format,
        or made by a different embedding model)."""
        if not isinstance(raw, str):
            return None
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(entry, dict):
            return None
        # Vectors from different models can't be compared, so skip them.
        if entry.get("model") != MODEL_NAME:
            return None
        if not isinstance(entry.get("response"), str):
            return None
        vector = entry.get("vector")
        if not isinstance(vector, list) or not vector:
            return None
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in vector):
            return None
        return entry

    def lookup(self, prompt: str) -> Optional[Tuple[str, str]]:
        """Return (response, description) on a hit, or None on a miss."""
        # 1. Fast path: the exact same prompt was asked before.
        entry = self._parse(self.db.get(KEY_PREFIX + hash_prompt(prompt)))
        if entry is not None:
            return entry["response"], "exact hit"

        # 2. Slow path: compare against every stored prompt, keep the best.
        query_vector = embed(prompt)
        best_score = 0.0
        best_response = None
        best_prompt = None          # remember which stored question matched

        for key in self.db.keys():
            if not key.startswith(KEY_PREFIX):
                continue
            entry = self._parse(self.db.get(key))
            if entry is None:
                continue

            stored_vector = np.array(entry["vector"])
            if stored_vector.shape != query_vector.shape:
                continue  # different vector size, can't compare

            score = cosine_similarity(query_vector, stored_vector)
            if score > best_score:
                best_score = score
                best_response = entry["response"]
                best_prompt = entry.get("prompt", "?")   

        if best_response is not None and best_score >= self.threshold:
            return (
                best_response,
                f"semantic hit (similarity {best_score:.2f}, matched: {best_prompt!r})",
            )
        return None

    def store(self, prompt: str, response: str) -> None:
        entry = {
            "prompt": prompt,
            "model": MODEL_NAME,
            "vector": embed(prompt).tolist(),   # numpy array -> plain list, so JSON can store it
            "response": response,
        }
        self.db.setex(KEY_PREFIX + hash_prompt(prompt), self.ttl, json.dumps(entry))

    def ask(self, prompt: str) -> Tuple[str, str]:
        """Return (response, status). Calls the model only on a miss."""
        hit = self.lookup(prompt)
        if hit is not None:
            return hit
        response = call_llm(prompt)
        self.store(prompt, response)
        return response, "miss (called the model)"


def run_demo() -> None:
    db = Database()
    db.load(CACHE_FILE)   # reuse earlier runs; on the first run this just returns an error string we ignore
    cache = SemanticCache(db)

    prompts = [
        "What is the capital of France?",
        "What's France's capital?",
        "Name the capital city of France.",
        "What is the capital of Germany?",
        "Is the capital of France big?",
    ]
    for prompt in prompts:
        start = time.time()
        response, status = cache.ask(prompt)
        elapsed = time.time() - start
        print(f"Prompt:   {prompt}")
        print(f"Result:   {status}  ({elapsed:.2f}s)")
        print(f"Response: {response}\n")

    db.save(CACHE_FILE)


if __name__ == "__main__":
    run_demo()
