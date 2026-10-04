"""
MiniDB — in-memory key-value store with TTL expiration and JSON persistence.

Stdlib only. No sqlite3, redis-py, tinydb, or any other db/cache package, per the project constraints.

"""

import json
import math
import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Union


# =====================================================================
# EXCEPTIONS
# =====================================================================

class MiniDBError(Exception):
    """Base class for MiniDB errors. Database methods raise these;
    the CLI loop is the only place that turns them into printed text.
    """
    pass


class KeyTypeError(MiniDBError):
    """Raised when a command hits a key of the wrong type
    (e.g. LPUSH on a key that already holds a string)."""
    pass


# =====================================================================
# DATABASE
# =====================================================================

class Database:
    """Key-value store with optional per-key TTL. Two value types: string
    and list.

    Each entry in self.data looks like:
        {
            "value": <str> or <list[str]>,
            "type": "string" or "list",
            "expires_at": <float, time.monotonic() timestamp> or None
        }

    TTL uses time.monotonic() instead of time.time() because it can't be
    moved backward/forward by clock adjustments or NTP syncs — it only
    measures elapsed time since the process started.

    The catch: monotonic time's zero point is arbitrary and
    resets on every process restart, so a raw expires_at is meaningless
    once saved to disk and reloaded later. To get around this, SAVE
    converts expires_at into "seconds remaining right now" (expires_in),
    and LOAD/RESTORE add that back onto the new process's monotonic
    clock. Net effect: TTLs survive a restart correctly, but time spent
    with the file closed isn't counted against the TTL — a key saved
    with 10s left still has 10s left on the next load, regardless of
    how long it sat on disk. Wall-clock-accurate expiry across restarts
    would need trusting time.time(), which defeats the point of using
    monotonic time in the first place."""

    VALID_TYPES = {"string", "list"}

    def __init__(self) -> None:
        self.data: Dict[str, Dict[str, Any]] = {}

    # -----------------------------------------------------------------
    # Expiration helpers
    # -----------------------------------------------------------------

    def _is_expired(self, key: str) -> bool:
        """True if `key` is present but its TTL has passed. Assumes key exists."""
        expires_at = self.data[key]["expires_at"]
        if expires_at is None:
            return False
        return time.monotonic() >= expires_at

    def _purge_if_expired(self, key: str) -> bool:
        """If `key` exists and is expired, delete it and return True.
        Used by GET/EXISTS/KEYS for lazy cleanup."""
        if key in self.data and self._is_expired(key):
            del self.data[key]
            return True
        return False

    # -----------------------------------------------------------------
    # Basic CRUD (string values)
    # -----------------------------------------------------------------

    def set(self, key: str, value: str) -> None:
        """Store a string with no expiration. Fully overwrites any previous
        entry at this key, including its old TTL or type."""
        self.data[key] = {"value": value, "type": "string", "expires_at": None}

    def setex(self, key: str, seconds: float, value: str) -> None:
        """Store a string that expires after `seconds` seconds."""
        self.data[key] = {
            "value": value,
            "type": "string",
            "expires_at": time.monotonic() + seconds,
        }

    def get(self, key: str) -> Optional[Any]:
        """Return the stored value (str or list), or None if missing/expired."""
        self._purge_if_expired(key)
        if key not in self.data:
            return None
        return self.data[key]["value"]

    def delete(self, key: str) -> bool:
        """Remove a key outright. Returns True if it existed (even if it
        had already expired — the user's intent to remove it is honored
        either way)."""
        if key in self.data:
            del self.data[key]
            return True
        return False

    def exists(self, key: str) -> bool:
        self._purge_if_expired(key)
        return key in self.data

    def clear(self) -> None:
        self.data.clear()

    def keys(self) -> List[str]:
        """All live (non-expired) keys. Expired keys are purged as a side effect."""
        for key in list(self.data.keys()):
            self._purge_if_expired(key)
        return list(self.data.keys())

    def size(self) -> int:
        return len(self.keys())

    # -----------------------------------------------------------------
    # List type
    # -----------------------------------------------------------------

    def _get_or_create_list(self, key: str) -> List[str]:
        """Used by LPUSH. Creates an empty list if the key is new; raises
        KeyTypeError if the key already holds a string."""
        self._purge_if_expired(key)
        if key not in self.data:
            self.data[key] = {"value": [], "type": "list", "expires_at": None}
        elif self.data[key]["type"] != "list":
            raise KeyTypeError(f"'{key}' holds a {self.data[key]['type']}, not a list.")
        return self.data[key]["value"]

    def lpush(self, key: str, value: str) -> int:
        """Push `value` onto the FRONT of the list at `key` (creating the
        list if needed). Returns the new length."""
        lst = self._get_or_create_list(key)
        lst.insert(0, value)
        return len(lst)

    def lpop(self, key: str) -> Optional[str]:
        """Pop and return the front element of the list at `key`.
        Returns None if the key is missing/expired or the list is empty."""
        self._purge_if_expired(key)
        if key not in self.data:
            return None
        if self.data[key]["type"] != "list":
            raise KeyTypeError(f"'{key}' holds a {self.data[key]['type']}, not a list.")
        lst = self.data[key]["value"]
        if not lst:
            return None
        return lst.pop(0)

    # -----------------------------------------------------------------
    # Validation for data loaded from disk
    # -----------------------------------------------------------------

    @staticmethod
    def _validate_loaded_entry(entry: Any) -> Optional[Dict[str, Any]]:
        """Normalize one raw JSON entry into {"value", "type", "expires_in"},
        or return None if it's unusable so the caller can skip it instead
        of crashing. entry.get("type", "string") also keeps old files
        (saved before "type" existed) loadable."""
        if not isinstance(entry, dict):
            return None
        if "value" not in entry:
            return None

        value = entry["value"]
        entry_type = entry.get("type", "string")  # old files had no "type" field
        if entry_type not in Database.VALID_TYPES:
            return None
        if entry_type == "string" and not isinstance(value, str):
            return None
        if entry_type == "list" and not isinstance(value, list):
            return None

        expires_in = entry.get("expires_in")
        if expires_in is not None:
            # bool is a subclass of int in Python, so exclude it explicitly
            # or a JSON true/false would pass as a "valid" number.
            if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)):
                return None
            if math.isnan(expires_in) or math.isinf(expires_in):
                return None

        return {"value": value, "type": entry_type, "expires_in": expires_in}

    # -----------------------------------------------------------------
    # Persistence: SAVE / LOAD / RESTORE
    # -----------------------------------------------------------------

    def _to_serializable(self) -> Dict[str, Dict[str, Any]]:
        """Build the JSON-ready dict for SAVE: expired keys are skipped,
        and each absolute expires_at is converted to a relative
        expires_in (seconds remaining, measured right now)."""
        now = time.monotonic()
        out: Dict[str, Dict[str, Any]] = {}
        for key in list(self.data.keys()):
            if self._purge_if_expired(key):
                continue
            entry = self.data[key]
            expires_at = entry["expires_at"]
            expires_in = None if expires_at is None else max(expires_at - now, 0)
            out[key] = {
                "value": entry["value"],
                "type": entry["type"],
                "expires_in": expires_in,
            }
        return out

    def save(self, filename: str) -> str:
        """Writes via a temp file + os.replace() so a crash mid-save can't
        leave a half-written, corrupted JSON file on disk."""
        if os.path.isdir(filename):
            return f"ERROR: '{filename}' is a directory, not a file."

        serializable = self._to_serializable()
        directory = os.path.dirname(os.path.abspath(filename)) or "."

        try:
            # delete=False: by default NamedTemporaryFile deletes itself when closed.
            with tempfile.NamedTemporaryFile(
                "w", dir=directory, delete=False, suffix=".tmp"
            ) as tmp_file:
                json.dump(serializable, tmp_file)
                tmp_path = tmp_file.name

            os.replace(tmp_path, filename)
            return f"Current database saved to '{filename}'."

        except FileNotFoundError:
            return f"ERROR: Path '{filename}' not found."
        except PermissionError:
            return f"ERROR: Permission denied for '{filename}'."
        except IsADirectoryError:
            return f"ERROR: '{filename}' is a directory, not a file."
        except OSError as e:
            # Broad catch-all for anything else OS-related (disk full,
            # read-only filesystem, etc.) so SAVE never crashes the REPL.
            return f"ERROR: Could not save to '{filename}' ({e})."

    def _load_entries(self, filename: str) -> Union[str, Dict[str, Dict[str, Any]]]:
        """Returns an error string, or a dict of validated entries ready
        to merge/replace with."""
        if os.path.isdir(filename):
            return f"ERROR: '{filename}' is a directory, not a file."

        try:
            with open(filename, "r") as file:
                loaded_data = json.load(file)
        except FileNotFoundError:
            return f"ERROR: File {filename} not found."
        except json.JSONDecodeError:
            return f"ERROR: File {filename} contains invalid JSON."
        except PermissionError:
            return f"ERROR: Permission denied for '{filename}'."
        except IsADirectoryError:
            return f"ERROR: '{filename}' is a directory, not a file."
        except OSError as e:
            return f"ERROR: Could not read '{filename}' ({e})."

        if not isinstance(loaded_data, dict):
            return f"ERROR: File {filename} does not contain a valid MiniDB database."

        now = time.monotonic()
        result: Dict[str, Dict[str, Any]] = {}

        for key, raw_entry in loaded_data.items():
            normalized = self._validate_loaded_entry(raw_entry)
            if normalized is None:
                continue  # skip corrupted / unrecognized entries, don't crash

            expires_in = normalized["expires_in"]
            if expires_in is not None and expires_in <= 0:
                continue  # it expired while the file was sitting on disk

            expires_at = None if expires_in is None else now + expires_in
            result[key] = {
                "value": normalized["value"],
                "type": normalized["type"],
                "expires_at": expires_at,
            }

        return result

    def load(self, filename: str) -> str:
        """MERGE: entries from the file overwrite same-named keys
        in the current database; keys only present in the current database
        are left untouched."""
        entries = self._load_entries(filename)
        if isinstance(entries, str):
            return entries  # it was an error message
        self.data.update(entries)
        return f"Data from '{filename}' merged into current database."

    def restore(self, filename: str) -> str:
        """REPLACE: the current database is discarded entirely
        and replaced with the file's contents."""
        entries = self._load_entries(filename)
        if isinstance(entries, str):
            return entries
        self.data = entries
        return f"Current database replaced with data from '{filename}'."


# =====================================================================
# CLI / REPL
# =====================================================================

VERSION = "MiniDB 1.0"

HELP_TEXT = """\
MiniDB - available commands (case-insensitive; keys are case-sensitive):

  SET <key> <value>              Store a string (no expiration)
  SETEX <key> <seconds> <value>  Store a string that expires after <seconds>
  GET <key>                      Retrieve a value
  DEL <key>                      Delete a key
  EXISTS <key>                   Check whether a key exists (true/false)
  LPUSH <key> <value>            Push a value to the front of a list
  LPOP <key>                     Pop a value from the front of a list
  KEYS                           List all live keys
  SIZE                           Count all live keys
  CLEAR                          Remove everything
  SAVE <filename>                Write the database to a JSON file
  LOAD <filename>                Merge a JSON file into the database
  RESTORE <filename>             Replace the database with a JSON file
  HELP                           Show this message
  VERSION                        Show MiniDB's version
  EXIT / QUIT                    Quit MiniDB

Known limitations:
  * Keys can't contain spaces or quote characters (" or '). Quotes have
    no special grouping meaning in this parser. Using them would
    silently create a garbage key, so they're rejected outright. Values
    can contain spaces and quotes freely; everything after the key is
    taken as the value.
  * TTLs are stored as "seconds remaining", not a fixed clock time, so
    they survive SAVE/LOAD/RESTORE regardless of system clock changes.
    Time elapsed while MiniDB is closed isn't tracked.
  * No concurrency protection. Two MiniDB processes saving to the same
    file will clobber each other, last write wins.
  * No enforced size limits on keys/values/files.
"""


def format_value(value: Any) -> str:
    """Turn a stored value (string or list) into printable text."""
    if isinstance(value, list):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return str(value)


def contains_quote(key: str) -> bool:
    """Reject keys with a literal quote character."""
    return '"' in key or "'" in key


def run() -> None:
    db = Database()
    print(f"{VERSION} — type HELP for commands, EXIT to quit.")

    while True:
        try:
            command = input("MiniDB> ").strip()
        except EOFError:
            # Lets Ctrl+Z/Ctrl+D exit cleanly instead of raising an unhandled error.
            print()
            break

        if command == "":
            continue

        parts = command.split(maxsplit=2)  # [OPERATION, key, rest-of-line]
        operation = parts[0].upper()       # commands case-insensitive, keys case-sensitive

        try:
            if operation in ("EXIT", "QUIT"):
                break

            elif operation == "HELP":
                print(HELP_TEXT)

            elif operation == "VERSION":
                print(VERSION)

            elif operation == "SET":
                if len(parts) != 3:
                    print("ERROR: SET requires a key and a value.")
                    continue
                if contains_quote(parts[1]):
                    print("ERROR: Keys cannot contain quote characters.")
                    continue
                db.set(parts[1], parts[2])
                print("OK")

            elif operation == "SETEX":
                setex_parts = command.split(maxsplit=3)
                if len(setex_parts) != 4:
                    print("ERROR: SETEX requires a key, expiration time, and value.")
                    continue

                key = setex_parts[1]
                if contains_quote(key):
                    print("ERROR: Keys cannot contain quote characters.")
                    continue

                try:
                    seconds = float(setex_parts[2])
                except ValueError:
                    print("ERROR: Expiration time must be a number.")
                    continue

                if math.isnan(seconds) or math.isinf(seconds):
                    print("ERROR: Expiration time must be a finite number.")
                    continue
                if seconds <= 0:
                    print("ERROR: Expiration time must be greater than 0.")
                    continue

                db.setex(key, seconds, setex_parts[3])
                print("OK")

            elif operation == "GET":
                if len(parts) != 2:
                    print("ERROR: GET requires exactly one key.")
                    continue
                if contains_quote(parts[1]):
                    print("ERROR: Keys cannot contain quote characters.")
                    continue
                result = db.get(parts[1])
                print("ERROR: Key not found." if result is None else format_value(result))

            elif operation == "DEL":
                if len(parts) != 2:
                    print("ERROR: DEL requires exactly one key.")
                    continue
                if contains_quote(parts[1]):
                    print("ERROR: Keys cannot contain quote characters.")
                    continue
                print("OK" if db.delete(parts[1]) else "ERROR: Key not found.")

            elif operation == "EXISTS":
                if len(parts) != 2:
                    print("ERROR: EXISTS requires exactly one key.")
                    continue
                if contains_quote(parts[1]):
                    print("ERROR: Keys cannot contain quote characters.")
                    continue
                print("true" if db.exists(parts[1]) else "false")

            elif operation == "LPUSH":
                if len(parts) != 3:
                    print("ERROR: LPUSH requires a key and a value.")
                    continue
                if contains_quote(parts[1]):
                    print("ERROR: Keys cannot contain quote characters.")
                    continue
                new_len = db.lpush(parts[1], parts[2])
                print(f"OK (length={new_len})")

            elif operation == "LPOP":
                if len(parts) != 2:
                    print("ERROR: LPOP requires exactly one key.")
                    continue
                if contains_quote(parts[1]):
                    print("ERROR: Keys cannot contain quote characters.")
                    continue
                result = db.lpop(parts[1])
                print("ERROR: Key not found or list empty." if result is None else result)

            elif operation == "CLEAR":
                if len(parts) != 1:
                    print("ERROR: CLEAR does not take arguments.")
                    continue
                db.clear()
                print("Current database cleared.")

            elif operation == "KEYS":
                if len(parts) != 1:
                    print("ERROR: KEYS does not take arguments.")
                    continue
                for k in db.keys():
                    print(k)

            elif operation == "SIZE":
                if len(parts) != 1:
                    print("ERROR: SIZE does not take arguments.")
                    continue
                print(db.size())

            elif operation == "SAVE":
                if len(parts) != 2:
                    print("ERROR: SAVE requires a file name.")
                    continue
                print(db.save(parts[1]))

            elif operation == "LOAD":
                if len(parts) != 2:
                    print("ERROR: LOAD requires a file name.")
                    continue
                print(db.load(parts[1]))

            elif operation == "RESTORE":
                if len(parts) != 2:
                    print("ERROR: RESTORE requires a file name.")
                    continue
                print(db.restore(parts[1]))

            else:
                print(f"ERROR: Unknown command '{parts[0]}'.")

        except KeyTypeError as e:
            print(f"ERROR: {e}")
        except MiniDBError as e:
            print(f"ERROR: {e}")


if __name__ == "__main__":
    run()
