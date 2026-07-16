"""Regression guards for the unbounded-memory bug.

Reading a JSONL file whole (`readlines()`) made RSS scale with the on-disk log size and,
for lines over pymalloc's 512-byte threshold, glibc never returned that heap to the OS.
An idle worker held GBs. These tests fail loudly if a whole-file read comes back.
"""
from __future__ import annotations

import json
import sys

import pytest

from app.change_log import ChangeLogStore
from app.log_manager import LogStore

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="RSS is read from /proc; the deployment target is Linux",
)

# Long lines are the pathological case: >512 B bypasses pymalloc and goes to glibc malloc,
# which could not trim the freed heap afterwards.
LINE_PADDING = 1200
LOG_SIZE_BYTES = 256 * 1024 * 1024
# Old code retained ~1.1x the file size (~290 MB here). Bounded reads should stay flat.
MAX_GROWTH_MB = 64


def rss_mb() -> float:
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    raise RuntimeError("VmRSS not found")


def write_big_log(path, total_bytes: int, padding: int) -> int:
    line = json.dumps({
        "timestamp": 1_700_000_000.0,
        "level": "ERROR",
        "logger": "app.poller",
        "source": "Poller",
        "message": "Unexpected error polling 'Sonarr': " + "x" * padding,
    }, ensure_ascii=False) + "\n"
    count = total_bytes // len(line)
    with path.open("w", encoding="utf-8") as fh:
        for _ in range(count):
            fh.write(line)
    return count


def test_log_store_startup_memory_is_independent_of_log_size(tmp_path):
    """Seeding the ring buffer must not scale with the file on disk."""
    path = tmp_path / "app-log.jsonl"
    written = write_big_log(path, LOG_SIZE_BYTES, LINE_PADDING)
    assert path.stat().st_size > 200 * 1024 * 1024

    before = rss_mb()
    store = LogStore(path=str(path), maxlen=5000, max_bytes=0)  # max_bytes=0 disables rotation
    growth = rss_mb() - before

    assert len(store._buffer) == 5000, "must still seed the buffer from the tail"
    assert growth < MAX_GROWTH_MB, (
        f"LogStore grew RSS by {growth:.0f} MB reading a "
        f"{path.stat().st_size / 1024 / 1024:.0f} MB log ({written:,} lines) — "
        "memory is scaling with file size again"
    )


def test_change_log_recent_memory_is_independent_of_file_size(tmp_path):
    path = tmp_path / "change-log.jsonl"
    write_big_log(path, LOG_SIZE_BYTES, LINE_PADDING)

    store = ChangeLogStore(str(path), max_bytes=0)
    before = rss_mb()
    entries = store.recent(limit=200)
    growth = rss_mb() - before

    assert len(entries) == 200
    assert growth < MAX_GROWTH_MB, (
        f"ChangeLogStore.recent() grew RSS by {growth:.0f} MB on a large change log"
    )


def test_repeated_reads_do_not_grow_memory(tmp_path):
    """Repeated cycles must return to baseline rather than climbing monotonically."""
    path = tmp_path / "change-log.jsonl"
    write_big_log(path, 64 * 1024 * 1024, LINE_PADDING)
    store = ChangeLogStore(str(path), max_bytes=0)

    store.recent(limit=200)  # let one-off allocations settle
    baseline = rss_mb()

    for _ in range(25):
        store.recent(limit=200)

    growth = rss_mb() - baseline
    assert growth < 16, f"RSS climbed {growth:.0f} MB over 25 read cycles"


def test_scan_over_large_library_does_not_retain_memory(tmp_path):
    """A scan over a 50k-item library must release the library when it returns."""
    from app.config import ServerConfig
    from app.poller import ServerRunner

    class FakeRadarrClient:
        base_url = "http://radarr.local"
        label = "Radarr"

        def __init__(self, items):
            self._items = items

        def get_items(self):
            # A fresh payload each cycle, as a real HTTP response would be.
            return [dict(i) for i in self._items]

        def unmonitor_item(self, item):  # pragma: no cover - nothing is over cutoff
            raise AssertionError("no item in this fixture should be unmonitored")

    library = [
        {
            "id": i,
            "title": f"Movie {i}",
            "year": 2000 + (i % 25),
            "titleSlug": f"movie-{i}",
            "monitored": True,
            "hasFile": True,
            "overview": "x" * 600,  # nested payloads are large in real Radarr data
            "movieFile": {"id": i, "qualityCutoffNotMet": True, "quality": {}},
        }
        for i in range(50_000)
    ]

    runner = ServerRunner(
        "Radarr",
        settings_store=None,
        change_log_store=ChangeLogStore(str(tmp_path / "change-log.jsonl")),
    )
    server = ServerConfig(name="Radarr", type="radarr", url="http://radarr.local", api_key="k")
    client = FakeRadarrClient(library)

    runner._process_radarr(server, client)  # warm up
    baseline = rss_mb()

    for _ in range(5):
        count, checked, _ = runner._process_radarr(server, client)
        assert (count, checked) == (0, 50_000)

    growth = rss_mb() - baseline
    assert growth < 300, f"5 scans over a 50k-item library grew RSS by {growth:.0f} MB"
