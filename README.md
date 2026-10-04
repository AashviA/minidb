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

`SET <key> <value>` — store a string
`SETEX <key> <seconds> <value>` — store a string that expires after N seconds
`GET <key>` — get a value
`DEL <key>` — delete a key
`EXISTS <key>` — check if a key exists
`LPUSH <key> <value>` — add a value to the front of a list
`LPOP <key>` — remove and return the front of a list
`KEYS` — show all current keys
`SIZE` — count current keys
`CLEAR` — wipe everything
`SAVE <filename>` — save the database to a file
`LOAD <filename>` — load a file and merge it into the current database
`RESTORE <filename>` — load a file and replace the current database with it
`HELP` — list all commands
`EXIT` / `QUIT` — quit

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
