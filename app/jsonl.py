from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator

# Reading the whole of a JSONL file into memory is what caused the container to hold
# several GB of RSS for the life of the process: log lines over 512 bytes bypass pymalloc
# and are allocated by glibc malloc, and freeing them does not return the heap to the OS
# while later allocations sit above them. Every read here is bounded instead.

DEFAULT_TAIL_MAX_BYTES = 16 * 1024 * 1024
_READ_BLOCK = 256 * 1024


def env_bytes(name: str, default: int) -> int:
    """Read a byte-size setting from the environment, falling back to default."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def tail_lines(
    path: Path,
    max_lines: int,
    max_bytes: int = DEFAULT_TAIL_MAX_BYTES,
) -> list[str]:
    """Return up to the last `max_lines` lines, reading at most `max_bytes` from the end.

    Peak memory is bounded by max_bytes regardless of how large the file is.
    """
    if max_lines <= 0:
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell()
            chunks: list[bytes] = []
            read_total = 0
            newlines = 0
            # Walk backwards from EOF until we have enough lines or hit the byte cap.
            while pos > 0 and newlines <= max_lines and read_total < max_bytes:
                size = min(_READ_BLOCK, pos, max_bytes - read_total)
                pos -= size
                fh.seek(pos)
                chunk = fh.read(size)
                chunks.append(chunk)
                read_total += len(chunk)
                newlines += chunk.count(b"\n")
            data = b"".join(reversed(chunks))
    except OSError:
        return []

    if not data:
        return []

    # A backwards read almost always starts mid-line; drop that partial fragment.
    if pos > 0:
        _, sep, rest = data.partition(b"\n")
        data = rest if sep else b""

    # Strip the trailing newline, otherwise split() yields a phantom empty final
    # element that would eat one slot of max_lines and drop the newest entry.
    data = data.rstrip(b"\n")
    if not data:
        return []

    raw_lines = data.split(b"\n")
    out: list[str] = []
    for raw in raw_lines[-max_lines:]:
        if not raw.strip():
            continue
        try:
            out.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            continue
    return out


def tail_entries(
    path: Path,
    max_lines: int,
    max_bytes: int = DEFAULT_TAIL_MAX_BYTES,
) -> list[dict[str, Any]]:
    """Tail a JSONL file and decode each line, skipping anything unparseable."""
    entries: list[dict[str, Any]] = []
    for line in tail_lines(path, max_lines, max_bytes):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def iter_entries(path: Path) -> Iterator[dict[str, Any]]:
    """Stream a JSONL file one entry at a time, never holding more than one line."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield payload
    except OSError:
        return


def remove_backups(path: Path, backup_count: int) -> None:
    """Delete rotated backups of `path`, so clearing a log really clears it."""
    for index in range(1, max(backup_count, 0) + 1):
        try:
            path.with_name(path.name + f".{index}").unlink(missing_ok=True)
        except OSError:
            pass


def rotate_if_needed(path: Path, max_bytes: int, backup_count: int = 1) -> bool:
    """Roll `path` to `path.1`, `path.2`, ... once it exceeds max_bytes. Returns True if rotated."""
    if max_bytes <= 0:
        return False
    try:
        if path.stat().st_size <= max_bytes:
            return False
    except OSError:
        return False

    try:
        if backup_count <= 0:
            path.unlink(missing_ok=True)
            return True
        # Shift existing backups down; the oldest falls off the end.
        oldest = path.with_name(path.name + f".{backup_count}")
        oldest.unlink(missing_ok=True)
        for index in range(backup_count - 1, 0, -1):
            src = path.with_name(path.name + f".{index}")
            if src.exists():
                os.replace(src, path.with_name(path.name + f".{index + 1}"))
        os.replace(path, path.with_name(path.name + ".1"))
        return True
    except OSError:
        return False
