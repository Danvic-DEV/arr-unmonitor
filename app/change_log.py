from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from .jsonl import env_bytes, iter_entries, remove_backups, rotate_if_needed, tail_entries

logger = logging.getLogger(__name__)

DEFAULT_CHANGE_LOG_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_CHANGE_LOG_BACKUP_COUNT = 2


class ChangeLogStore:
    def __init__(
        self,
        path: str,
        max_bytes: int | None = None,
        backup_count: int | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._max_bytes = (
            env_bytes("CHANGE_LOG_MAX_BYTES", DEFAULT_CHANGE_LOG_MAX_BYTES)
            if max_bytes is None
            else max_bytes
        )
        self._backup_count = (
            DEFAULT_CHANGE_LOG_BACKUP_COUNT if backup_count is None else backup_count
        )

    def append(self, entry: dict[str, object]) -> None:
        enriched = {"timestamp": time.time(), **entry}
        line = json.dumps(enriched, ensure_ascii=False)
        with self._lock:
            rotate_if_needed(self.path, self._max_bytes, self._backup_count)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
        logger.debug("Change log entry appended: %s", entry.get("title", ""))

    def recent(self, limit: int = 200) -> list[dict[str, object]]:
        """Return the newest `limit` entries, newest first, reading only the file's tail."""
        if limit <= 0 or not self.path.exists():
            return []

        with self._lock:
            entries = tail_entries(self.path, limit)
        entries.reverse()
        return entries

    def clear(self) -> None:
        with self._lock:
            with self.path.open("w", encoding="utf-8"):
                pass
            remove_backups(self.path, self._backup_count)

    def count_since(self, since_timestamp: float) -> int:
        if not self.path.exists():
            return 0

        count = 0
        with self._lock:
            for payload in iter_entries(self.path):
                timestamp = payload.get("timestamp")
                if isinstance(timestamp, (int, float)) and float(timestamp) >= since_timestamp:
                    count += 1
        return count

    def count_since_by_server(self, since_timestamp: float) -> dict[str, int]:
        """Return {server_name: count} of changes since a timestamp."""
        if not self.path.exists():
            return {}

        counts: dict[str, int] = {}
        with self._lock:
            for payload in iter_entries(self.path):
                timestamp = payload.get("timestamp")
                if isinstance(timestamp, (int, float)) and float(timestamp) >= since_timestamp:
                    service = payload.get("service", "")
                    if service:
                        counts[service] = counts.get(service, 0) + 1
        return counts
