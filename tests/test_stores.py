from __future__ import annotations

import json
import logging
import time

from app.change_log import ChangeLogStore
from app.log_manager import LogStore, setup_logging


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── LogStore ──────────────────────────────────────────────────────────────


def test_log_store_seeds_buffer_from_tail_of_file(tmp_path):
    path = tmp_path / "app-log.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for i in range(100):
            fh.write(json.dumps({"level": "INFO", "message": f"m{i}", "source": "Poller"}) + "\n")

    store = LogStore(path=str(path), maxlen=10)

    # Newest 10, newest-first out of recent()
    assert [e["message"] for e in store.recent(limit=10)] == [f"m{i}" for i in range(99, 89, -1)]


def test_log_store_seed_is_capped_by_maxlen(tmp_path):
    path = tmp_path / "app-log.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for i in range(500):
            fh.write(json.dumps({"level": "INFO", "message": f"m{i}"}) + "\n")

    store = LogStore(path=str(path), maxlen=25)

    assert len(store._buffer) == 25


def test_log_store_append_persists_and_buffers(tmp_path):
    path = tmp_path / "app-log.jsonl"
    store = LogStore(path=str(path), maxlen=10)

    store.append({"level": "INFO", "message": "hello", "source": "App"})

    assert read_lines(path) == [{"level": "INFO", "message": "hello", "source": "App"}]
    assert store.recent()[0]["message"] == "hello"


def test_log_store_recent_filters_by_level_and_source(tmp_path):
    store = LogStore(path=None, maxlen=10)
    store.append({"level": "DEBUG", "message": "d", "source": "Poller"})
    store.append({"level": "ERROR", "message": "e", "source": "Radarr"})

    assert [e["message"] for e in store.recent(min_level="ERROR")] == ["e"]
    assert [e["message"] for e in store.recent(source="poller")] == ["d"]


def test_log_store_rotates_when_over_cap(tmp_path):
    path = tmp_path / "app-log.jsonl"
    store = LogStore(path=str(path), maxlen=10, max_bytes=500, backup_count=1)

    for i in range(40):
        store.append({"level": "INFO", "message": "x" * 50, "i": i})

    assert path.stat().st_size <= 500 + 200  # cap + at most one over-cap write
    assert (tmp_path / "app-log.jsonl.1").exists()


def test_log_store_clear_removes_backups(tmp_path):
    path = tmp_path / "app-log.jsonl"
    store = LogStore(path=str(path), maxlen=10, max_bytes=500, backup_count=1)
    for i in range(40):
        store.append({"level": "INFO", "message": "x" * 50, "i": i})
    assert (tmp_path / "app-log.jsonl.1").exists()

    store.clear()

    assert path.read_text(encoding="utf-8") == ""
    assert not (tmp_path / "app-log.jsonl.1").exists()
    assert store.recent() == []


def test_log_store_rotates_oversized_file_at_startup(tmp_path):
    path = tmp_path / "app-log.jsonl"
    path.write_text("x" * 5_000, encoding="utf-8")

    LogStore(path=str(path), maxlen=10, max_bytes=1_000, backup_count=1)

    assert (tmp_path / "app-log.jsonl.1").exists()
    assert not path.exists() or path.stat().st_size == 0


def test_setup_logging_routes_records_into_store(tmp_path):
    root = logging.getLogger()
    original = list(root.handlers)
    try:
        store = setup_logging(str(tmp_path / "app-log.jsonl"))
        logging.getLogger("app.poller").info("scan finished")

        entries = store.recent()
        assert entries[0]["message"] == "scan finished"
        assert entries[0]["source"] == "Poller"
    finally:
        root.handlers = original


# ── ChangeLogStore ────────────────────────────────────────────────────────


def test_change_log_recent_returns_newest_first(tmp_path):
    store = ChangeLogStore(str(tmp_path / "change-log.jsonl"))
    for i in range(10):
        store.append({"title": f"t{i}", "service": "Radarr"})

    assert [e["title"] for e in store.recent(limit=3)] == ["t9", "t8", "t7"]


def test_change_log_recent_limit_zero_and_missing_file(tmp_path):
    store = ChangeLogStore(str(tmp_path / "change-log.jsonl"))

    assert store.recent(limit=0) == []
    assert store.recent(limit=5) == []  # file not created until first append


def test_change_log_append_adds_timestamp(tmp_path):
    store = ChangeLogStore(str(tmp_path / "change-log.jsonl"))
    store.append({"title": "Movie"})

    entry = store.recent()[0]
    assert isinstance(entry["timestamp"], float)
    assert entry["title"] == "Movie"


def test_change_log_counts_since(tmp_path):
    store = ChangeLogStore(str(tmp_path / "change-log.jsonl"))
    store.append({"title": "old", "service": "Radarr"})

    time.sleep(0.02)
    cutoff = time.time()
    time.sleep(0.02)

    store.append({"title": "new1", "service": "Radarr"})
    store.append({"title": "new2", "service": "Sonarr"})

    assert store.count_since(cutoff) == 2
    assert store.count_since_by_server(cutoff) == {"Radarr": 1, "Sonarr": 1}


def test_change_log_clear_removes_backups(tmp_path):
    path = tmp_path / "change-log.jsonl"
    store = ChangeLogStore(str(path), max_bytes=500, backup_count=2)
    for i in range(40):
        store.append({"title": "x" * 50, "service": "Radarr"})
    assert (tmp_path / "change-log.jsonl.1").exists()

    store.clear()

    assert store.recent() == []
    assert not (tmp_path / "change-log.jsonl.1").exists()
    assert not (tmp_path / "change-log.jsonl.2").exists()
