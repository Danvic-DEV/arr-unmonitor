from __future__ import annotations

import json

from app.jsonl import iter_entries, remove_backups, rotate_if_needed, tail_entries, tail_lines


def write_jsonl(path, entries):
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def test_tail_lines_returns_last_n_in_file_order(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text("".join(f"line {i}\n" for i in range(100)), encoding="utf-8")

    assert tail_lines(path, 3) == ["line 97", "line 98", "line 99"]


def test_tail_lines_handles_file_shorter_than_limit(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text("a\nb\n", encoding="utf-8")

    assert tail_lines(path, 50) == ["a", "b"]


def test_tail_lines_on_empty_and_missing_file(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")

    assert tail_lines(empty, 10) == []
    assert tail_lines(tmp_path / "missing.jsonl", 10) == []


def test_tail_lines_drops_partial_leading_line(tmp_path):
    """A backwards read starts mid-line; that fragment must not be returned as a line."""
    path = tmp_path / "log.jsonl"
    # Each line is far bigger than one read block, forcing a mid-line start.
    path.write_text("".join(f"{i:06d}" + "x" * 400_000 + "\n" for i in range(4)), encoding="utf-8")

    lines = tail_lines(path, 2)

    assert len(lines) == 2
    assert lines[0].startswith("000002")
    assert lines[1].startswith("000003")
    assert all(len(line) == 400_006 for line in lines)


def test_tail_lines_without_trailing_newline(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text("a\nb\nc", encoding="utf-8")

    assert tail_lines(path, 2) == ["b", "c"]


def test_tail_lines_preserves_utf8_across_block_boundary(tmp_path):
    path = tmp_path / "log.jsonl"
    entries = [f"série {i} — ünïcode ✓" for i in range(500)]
    path.write_text("\n".join(entries) + "\n", encoding="utf-8")

    assert tail_lines(path, 2) == entries[-2:]


def test_tail_lines_respects_byte_cap(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text("".join("x" * 999 + "\n" for _ in range(1000)), encoding="utf-8")

    # 5 KB cap cannot yield 1000 lines; it must return only what fits, not read the file.
    lines = tail_lines(path, 1000, max_bytes=5_000)

    assert 0 < len(lines) < 1000


def test_tail_entries_skips_malformed_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text(
        '{"a": 1}\nnot json\n[1,2]\n{"b": 2}\n',
        encoding="utf-8",
    )

    assert tail_entries(path, 10) == [{"a": 1}, {"b": 2}]


def test_iter_entries_streams_dicts(tmp_path):
    path = write_jsonl(tmp_path / "log.jsonl", [{"i": i} for i in range(5)])

    assert list(iter_entries(path)) == [{"i": i} for i in range(5)]


def test_iter_entries_on_missing_file(tmp_path):
    assert list(iter_entries(tmp_path / "nope.jsonl")) == []


def test_rotate_if_needed_below_cap_is_noop(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text("small\n", encoding="utf-8")

    assert rotate_if_needed(path, max_bytes=1_000) is False
    assert path.read_text(encoding="utf-8") == "small\n"


def test_rotate_if_needed_moves_file_to_backup(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text("x" * 2_000, encoding="utf-8")

    assert rotate_if_needed(path, max_bytes=1_000, backup_count=1) is True
    assert not path.exists()
    assert (tmp_path / "log.jsonl.1").read_text(encoding="utf-8") == "x" * 2_000


def test_rotate_if_needed_shifts_backups_and_drops_oldest(tmp_path):
    path = tmp_path / "log.jsonl"
    (tmp_path / "log.jsonl.1").write_text("first", encoding="utf-8")
    (tmp_path / "log.jsonl.2").write_text("oldest", encoding="utf-8")
    path.write_text("newest" + "x" * 2_000, encoding="utf-8")

    rotate_if_needed(path, max_bytes=1_000, backup_count=2)

    assert (tmp_path / "log.jsonl.1").read_text(encoding="utf-8").startswith("newest")
    assert (tmp_path / "log.jsonl.2").read_text(encoding="utf-8") == "first"
    # backup_count=2 means "oldest" falls off the end
    assert not (tmp_path / "log.jsonl.3").exists()


def test_rotate_with_zero_backups_discards(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text("x" * 2_000, encoding="utf-8")

    assert rotate_if_needed(path, max_bytes=1_000, backup_count=0) is True
    assert not path.exists()
    assert not (tmp_path / "log.jsonl.1").exists()


def test_remove_backups(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text("live", encoding="utf-8")
    (tmp_path / "log.jsonl.1").write_text("b1", encoding="utf-8")
    (tmp_path / "log.jsonl.2").write_text("b2", encoding="utf-8")

    remove_backups(path, backup_count=2)

    assert path.exists()
    assert not (tmp_path / "log.jsonl.1").exists()
    assert not (tmp_path / "log.jsonl.2").exists()
