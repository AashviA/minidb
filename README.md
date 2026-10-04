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
