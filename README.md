# MiniDB & Semantic Cache

An in-memory key-value database built from scratch in pure Python, plus a
semantic cache on top of it that answers repeated and reworded questions in
about 15 milliseconds instead of making a roughly 1-second LLM API call.

## At a glance

| | Model call (cache miss) | Cache hit |
|---|---|---|
| Latency | ~0.8-1.1 s | ~15-19 ms |
| API tokens used | Yes | None |
| Speed-up | | ~50-65x faster |

Tested on 40 hostel-FAQ questions with a similarity threshold of 0.70:
6 of 40 calls (15%) were served from the cache, 5 of the 14 reworded
questions were matched, and 1 answer was wrong. Full results are
[below](#results).

The project has two parts:

1. **MiniDB (the core).** A command-line key-value database with key
   expiration (TTL), list values and JSON persistence. Standard library
   only: no SQLite, Redis or caching packages.
2. **A semantic cache for LLM calls (the extension).** It uses MiniDB as its
   storage layer to cache answers from a real LLM API (Groq), and reuses an
   answer when a *similar* question comes in, matched with sentence
   embeddings.

## The problem

Calling an LLM is slow, it costs tokens, and APIs rate-limit you. In my
tests a call to Groq's `gpt-oss-20b` took about 0.8-1.1 seconds on average,
and on the free tier I hit the tokens-per-minute limit (HTTP 429) after only
a handful of requests.

Meanwhile, apps often get the same question again and again in different
words. A hostel FAQ bot might see "What time is curfew?", "What's the
deadline to be back at night?" and "What time do we have to be back?" in one
evening. A normal cache only helps when the text is identical, so it misses
all of them, and each one pays for a fresh API call.

## The idea

Turn each question into a vector (a list of numbers) so that questions with
similar meaning land close together. When a new question arrives, check
whether it's close enough to one already answered. If it is, return the
stored answer. If not, call the LLM and store the new answer for next time.

The goal: serve repeated and reworded questions from the cache without
serving wrong answers, and measure honestly how well that works.

## How it works

```
Incoming question
   |
   v
Exact match in MiniDB? --yes--> return cached answer        (near-instant)
   | no
   v
Embed the question (all-MiniLM-L6-v2 -> 384 numbers)
   |
   v
Compare with every stored question (cosine similarity)
   |
   |-- best score >= threshold (0.70) --> return cached answer   (~15 ms)
   |
   '-- otherwise --> call the LLM API (Groq, gpt-oss-20b)        (~1 s)
                        |
                        '--> store question vector + answer in MiniDB
                             with a 24-hour TTL
```

**LLM API.** A cache miss sends an HTTPS request to Groq's
OpenAI-compatible chat-completions endpoint, authenticated with an API key
read from a `.env` file that is never committed. If Groq returns a
rate-limit error (HTTP 429), the code waits as long as Groq asks and
retries. Other errors show Groq's message instead of a bare status code. The
model name is a setting (`GROQ_MODEL`), because models get retired: the
first model I used, `llama-3.1-8b-instant`, was shut down by Groq while I
was building this.

**Embeddings.** An embedding model turns a sentence into a list of numbers
(a vector), so that sentences with similar meaning end up near each other.
I use `all-MiniLM-L6-v2` from the `sentence-transformers` library, which
produces 384 numbers per sentence and runs locally. Embedding needs no API
call and costs no tokens.

**Retrieval.** Finding a cached answer is a small nearest-neighbour search.
The new question's vector is compared with every stored question's vector
using cosine similarity (1.0 means the same direction, 0 means unrelated),
and the best match is returned if it scores above the threshold. I wrote the
similarity function and the lookup logic myself. It's a plain linear scan,
which is fine at this size but won't scale (see limitations).

**Storage.** Each cache entry (question, vector, answer, embedding model
name) is stored in MiniDB as a JSON string under a `sem:` key. The TTL from
`SETEX` makes old answers expire on their own, and `SAVE` / `LOAD` let the
cache survive restarts. Entries record which embedding model made them, and
entries from a different model, or corrupted ones, are skipped on load,
because vectors from different models can't be compared.

**Evaluation.** `eval_cache.py` replays a labelled list of questions
against an empty cache, times every call, and reports hits, wrong hits and
latency, so the threshold is chosen from data rather than guessed.

## Results

**Test set:** 40 hostel-FAQ questions, asked in order. 14 are rewordings of
an earlier question (should hit the cache) and 26 are distinct questions
(should not). A hit only counts as correct if the cached answer to an
earlier question would fully answer the new one. Related-but-different
questions (for example, "What happens if I am late?" after "What is the
deadline to enter?") are deliberately labelled as should-miss, because
they're the hardest case.

Each call is timed with `time.perf_counter()`, the embedding model is loaded
before timing starts, and every run starts with an empty cache.

| Threshold | Calls served from cache | Correct hits | Wrong answers | Avg cache hit | Avg model call |
|---|---|---|---|---|---|
| 0.60 | 13 / 40 | 8 of 14 | 5 | 14.6 ms | 784 ms |
| 0.65 | 9 / 40 | 5 of 14 | 4 | 14.9 ms | 978 ms |
| **0.70** | **6 / 40** | **5 of 14** | **1** | **15.4 ms** | **1014 ms** |
| 0.75 | 2 / 40 | 2 of 14 | 0 | 16.7 ms | 1045 ms |
| 0.80 | 1 / 40 | 1 of 14 | 0 | 18.8 ms | 1067 ms |

- **Latency:** a cache hit takes about 15-19 ms (this includes embedding the
  question and scanning the stored entries), and a model call takes about
  0.8-1.1 s. Hits are roughly 50-65x faster and save about a second each.
- **Best trade-off, threshold 0.70:** 15% of calls were served from the
  cache and 5 of 14 rewordings (36%) were matched, with 1 wrong answer. That
  one was a borderline case: "Can I stay out of the hostel for the weekend?"
  matched "Can I come back to the hostel at 11:30 PM?".
- **Safest, threshold 0.75:** no wrong answers, but only 2 of 40 calls (5%)
  were avoided.
- **Lowering the threshold backfires:** going from 0.70 to 0.60 catches 3
  more correct rewordings but raises wrong answers from 1 to 5.

## What I learned

- **No single threshold separates right from wrong.** Correct rewordings
  score between about 0.60 and 0.80, and wrong matches score between 0.62
  and 0.73. They overlap. Because every question is about the same hostel,
  related-but-different questions look similar to the model.
- **How you label the test set decides the result.** My first labelling
  counted loosely related questions as "should hit", which made some results
  look better and some worse than they were. I switched to a strict rule
  (the cached answer must fully answer the new question) and rewrote the
  set. The numbers above use that version.
- **Some questions shouldn't be cached.** "Right now", "tonight" and "today"
  change meaning depending on when you ask, so a cached answer goes stale. A
  real system would skip caching these or give them a short TTL.
- **On a free API tier, caching is more than a speed-up.** Every avoided call
  also avoids the tokens, and the rate-limit pressure, that come with it.

## Run it

**Quickstart: the core database** needs Python 3.7+ and nothing else:

```bash
python minidb.py
```

**The LLM cache demos** need Python 3.9+ and a free Groq API key from
console.groq.com.

1. Install the packages (the first run also downloads the embedding model,
   about 90 MB):

   ```bash
   pip install -r requirements-demos.txt
   ```

2. Create a file named `.env` in the project folder containing your key. It's
   listed in `.gitignore`, so it never gets committed:

   ```
   GROQ_API_KEY=your_key_here
   ```

   You can also set `GROQ_MODEL` in the same file to use a different Groq
   model.

3. Run a demo or the evaluation. The evaluation makes a real API call for
   every cache miss:

   ```bash
   python llm_cache.py                                 # exact-match cache demo
   python semantic_cache.py                            # semantic cache demo
   python eval_cache.py eval_questions_v2.json 0.70    # evaluation at a threshold
   ```

## MiniDB commands

`SET <key> <value>` — store a string, no expiration

`SETEX <key> <seconds> <value>` — store a string that expires after N seconds

`GET <key>` — retrieve a value

`DEL <key>` — delete a key

`EXISTS <key>` — check if a key exists

`LPUSH <key> <value>` — push a value to the front of a list

`LPOP <key>` — pop a value from the front of a list

`KEYS` — list all live keys

`SIZE` — count all live keys

`CLEAR` — remove everything

`SAVE <filename>` — write the database to a JSON file

`LOAD <filename>` — merge a JSON file into the current database

`RESTORE <filename>` — replace the current database with a JSON file

`HELP` — show the command reference

`VERSION` — show the version

`EXIT` / `QUIT` — quit

## Design decisions in the core database

**Why `time.monotonic()` instead of a normal clock?** A normal clock
(`time.time()`) can shift if your computer's clock gets adjusted, which
would make a key's expiration suddenly wrong. `time.monotonic()` only counts
forward, so it's safer for measuring how much time is left. The downside is
that it resets every time the program restarts, so the code converts each
TTL into "seconds remaining" before saving and re-applies that when loading.
One consequence: time that passes while the program is closed isn't counted
against a key's TTL.

**Why save to a temp file first?** If the program crashed halfway through
writing the save file, you'd be left with a half-written, corrupted JSON
file. Writing to a temporary file and swapping it in only once the write is
complete means the save file is always either fully old or fully new.

**Input handling.** Corrupted or old-format files, bad expiration values
(negative, zero, NaN, infinity) and wrong-type commands are rejected with a
clear message instead of crashing. Keys containing spaces or quote
characters are rejected too, because the parser doesn't support quoting and
would otherwise silently create a garbage key.

## Known limitations

- **The semantic cache can serve a wrong answer.** Matching is only as good
  as the embedding model and the threshold, and the results above show both
  have limits.
- **Lookup is a linear scan** (O(n)) over every stored entry. That's fine for
  a demo, but a larger cache would need a vector index such as FAISS or HNSW.
- **Small evaluation set:** 40 questions, 14 rewordings, one domain. One
  question moves a result by several percentage points, so small differences
  shouldn't be over-read.
- **Time-relative questions** ("today", "right now") can return stale
  answers until their TTL expires.
- Keys can't contain spaces or quote characters.
- There's no file locking: two copies of MiniDB writing to the same file
  overwrite each other.
- There are no limits on key, value or file size.

## What I'd try next

- Compare a larger embedding model on the same test set.
- Add a second check for borderline matches (for example, a cheap LLM call
  to confirm the cached answer fits) and measure whether it's worth the
  extra call.
- Skip caching for time-relative questions, or give them a short TTL.
- Replace the linear scan with a vector index and measure how lookup time
  grows with the size of the cache.

## Project files

- `minidb.py` — the key-value database and CLI (standard library only)
- `llm_cache.py` — exact-match LLM response cache and the Groq API call
- `semantic_cache.py` — embedding-based semantic cache
- `eval_cache.py` — evaluation and latency measurement
- `eval_questions_v2.json` — the labelled test set
- `requirements-demos.txt` — extra packages for the LLM demos
