# minidb

A command-line in-memory key-value store with TTL-based key expiration and JSON persistence, built from scratch in pure Python (no database libraries).
You can set values, get them back, give them an expiration time, and save/load everything to a file.

## What it can do

- Store and retrieve string values (`SET`, `GET`, `DEL`, `EXISTS`)
- Store values that auto-expire after N seconds (`SETEX`)
- Store lists (`LPUSH`, `LPOP`)
- Save the whole database to a JSON file and load it back later
- Handles a bunch of edge cases cleanly: bad input, corrupted files, invalid expiration times, etc., instead of crashing

## Commands

| Command | Description |
|---|---|
| `SET <key> <value>` | Store a string, no expiration |
| `SETEX <key> <seconds> <value>` | Store a string that expires after N seconds |
| `GET <key>` | Retrieve a value |
| `DEL <key>` | Delete a key |
| `EXISTS <key>` | Check if a key exists |
| `LPUSH <key> <value>` | Push a value to the front of a list |
| `LPOP <key>` | Pop a value from the front of a list |
| `KEYS` | List all live keys |
| `SIZE` | Count all live keys |
| `CLEAR` | Remove everything |
| `SAVE <filename>` | Write the database to a JSON file |
| `LOAD <filename>` | Merge a JSON file into the current database |
| `RESTORE <filename>` | Replace the current database with a JSON file |
| `HELP` | Show command reference |
| `VERSION` | Show version |
| `EXIT` / `QUIT` | Quit |

## How to run it

The core database needs only Python 3.7+ and nothing else:

```bash
python minidb.py
```

## Bonus: using MiniDB as an LLM response cache

`llm_cache.py` is an optional demo showing a real use for the TTL feature.
Calling an AI model is slow and costs money, so before each call it checks
whether the same prompt was already answered recently. If so, it returns the
stored answer instantly instead of calling the model again.

- The prompt is hashed (SHA-256) to make a safe cache key
- On a cache miss, it calls the Groq API and stores the reply with `SETEX`
- On a cache hit, it returns the stored reply with no API call

This demo needs two extra packages (the core database does not):

```bash
pip install requests python-dotenv
```

Create a `.env` file in the project folder containing your own key, which
is never committed to the repo:

```
GROQ_API_KEY=your_key_here
```

Then run:

```bash
python llm_cache.py
```

The first call takes a second or two (real API call). The second call with
the same prompt is near-instant, because it comes from the cache.

## A couple of interesting design decisions

**Why does it use `time.monotonic()` instead of a normal clock?**
A normal clock (`time.time()`) can shift. If your computer's
clock gets adjusted, a key's expiration could suddenly be wrong.
`time.monotonic()` only counts forward, so it's safer for
measuring how much time is left. The downside is that it resets
every time the program restarts, so saving and loading expiration
times took a bit of extra thought (the code converts a TTL into
"seconds remaining" before saving, then re-applies that when loading).

**Why save to a temp file first instead of writing directly?**
If the program crashed halfway through writing the save file, you'd be
left with a corrupted, half-written JSON file. Instead, it writes to a
separate temporary file first, and only swaps it in as the real file
once the write is fully done. That way the save file is always either
fully old or fully new, never broken.

## Known limitations

- Keys can't have spaces or quote marks in them.
- If you run two copies of MiniDB on the same file, they'll overwrite
  each other. There's no protection against that.
- No limit on how big a key, value, or file can be.
