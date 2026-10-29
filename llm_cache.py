"""
llm_cache.py uses MiniDB as a cache for LLM responses.

Before calling the model, we check if this exact prompt has been asked
before and hasn't expired yet. If so, we return the stored answer
instantly instead of making another API call.
"""

import hashlib
import os
import time

import requests
from dotenv import load_dotenv

from minidb import Database

# Reads the .env file and makes GROQ_API_KEY available via os.environ
load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")


def hash_prompt(prompt: str) -> str:
    """Turn a prompt into a short, fixed-length cache key (MiniDB keys
    can't contain spaces/quotes, and a hash sidesteps that entirely)."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def call_llm(prompt: str, max_retries: int = 3) -> str:
    """Send the prompt to Groq and return the reply. If we hit the rate
    limit (HTTP 429), wait and try again instead of crashing."""
    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY not found. Make sure you created a .env file "
            "with GROQ_API_KEY=your_key_here in the project folder."
        )

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": 600,   # caps tokens per call (reasoning tokens count too)
        "reasoning_effort": "low",      # less "thinking" = fewer tokens, faster
    }

    for attempt in range(max_retries + 1):
        response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)

        if response.status_code == 429 and attempt < max_retries:
            # Groq usually says how long to wait; fall back to 10 seconds.
            try:
                wait = float(response.headers.get("retry-after", 10))
            except ValueError:
                wait = 10.0
            print(f"  (rate limited, waiting {wait:.0f}s then retrying...)")
            time.sleep(wait + 1)
            continue

        if not response.ok:
            raise RuntimeError(f"Groq API error {response.status_code}: {response.text}")

        content = response.json()["choices"][0]["message"]["content"]
        if not content:
            raise RuntimeError(
                "Model returned an empty answer. It may have run out of "
                "tokens while reasoning; try raising max_completion_tokens."
            )
        return content

def cached_llm_call(db: Database, prompt: str, ttl_seconds: float = 300) -> tuple[str, bool]:
    """Returns (response_text, was_cache_hit)."""
    key = hash_prompt(prompt)
    cached = db.get(key)

    if cached is not None:
        return cached, True

    response = call_llm(prompt)
    db.setex(key, ttl_seconds, response)
    return response, False


def run_demo() -> None:
    db = Database()
    prompt = "What is the capital of France? Answer in one sentence."

    print("First call (expect a cache miss, API delay):")
    start = time.time()
    response, was_hit = cached_llm_call(db, prompt)
    elapsed = time.time() - start
    print(f"  Response: {response}")
    print(f"  Cache hit: {was_hit} | Took {elapsed:.2f}s\n")

    print("Second call, same prompt (expect a cache hit, near-instant):")
    start = time.time()
    response, was_hit = cached_llm_call(db, prompt)
    elapsed = time.time() - start
    print(f"  Response: {response}")
    print(f"  Cache hit: {was_hit} | Took {elapsed:.2f}s")


if __name__ == "__main__":
    run_demo()
