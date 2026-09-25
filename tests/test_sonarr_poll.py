from __future__ import annotations

import copy

from app.arr_client import SonarrClient
from app.change_log import ChangeLogStore
from app.config import ServerConfig, SettingsStore
from app.poller import ServerRunner


def _series(file_count: int = 1, size: int = 100) -> dict:
    return {
        "id": 1,
        "title": "Show",
        "titleSlug": "show",
        "monitored": True,
        "status": "continuing",
        "qualityProfileId": 1,
        "statistics": {
            "episodeFileCount": file_count,
            "episodeCount": 1,
            "totalEpisodeCount": 1,
            "sizeOnDisk": size,
        },
        "seasons": [{"seasonNumber": 1, "monitored": True, "statistics": {}}],
    }


def _episode(cutoff_not_met: bool) -> dict:
    return {
        "id": 10,
        "seasonNumber": 1,
        "episodeNumber": 1,
        "title": "Pilot",
        "monitored": True,
        "episodeFile": {"id": 5, "qualityCutoffNotMet": cutoff_not_met,
                        "quality": {"quality": {"name": "WEBDL-1080p"}}},
    }


class FakeSonarr(SonarrClient):
    def __init__(self, series: dict, episodes: list[dict]) -> None:
        super().__init__("http://sonarr", "key", label="Sonarr")
        self.series = series
        self.episodes = episodes
        self.episode_calls = 0
        self.unmonitored: list[int] = []

    def get_items(self):
        return [copy.deepcopy(self.series)]

    def get_episodes(self, series_id, include_episode_files=False):
        self.episode_calls += 1
        return copy.deepcopy(self.episodes)

    def unmonitor_episode(self, episode, series_title="", series_slug=""):
        self.unmonitored.append(episode["id"])


def _runner(tmp_path) -> ServerRunner:
    return ServerRunner(
        "Sonarr",
        SettingsStore(str(tmp_path / "settings.json")),
        ChangeLogStore(str(tmp_path / "change-log.jsonl")),
    )


def test_unchanged_clean_series_is_skipped_until_full_scan(tmp_path):
    runner = _runner(tmp_path)
    server = ServerConfig(name="Sonarr", type="sonarr")
    client = FakeSonarr(_series(), [_episode(cutoff_not_met=True)])

    runner._process_sonarr(server, client)
    runner._process_sonarr(server, client)
    assert client.episode_calls == 1

    runner._last_full_scan = 0.0
    runner._process_sonarr(server, client)
    assert client.episode_calls == 2


def test_changed_series_is_rescanned(tmp_path):
    runner = _runner(tmp_path)
    server = ServerConfig(name="Sonarr", type="sonarr")
    client = FakeSonarr(_series(size=100), [_episode(cutoff_not_met=True)])

    runner._process_sonarr(server, client)
    client.series = _series(size=200)
    client.episodes = [_episode(cutoff_not_met=False)]
    count, _, _ = runner._process_sonarr(server, client)

    assert client.episode_calls == 2
    assert count == 1
    assert client.unmonitored == [10]


def test_series_with_changes_is_not_cached(tmp_path):
    runner = _runner(tmp_path)
    server = ServerConfig(name="Sonarr", type="sonarr")
    client = FakeSonarr(_series(), [_episode(cutoff_not_met=False)])

    runner._process_sonarr(server, client)
    runner._process_sonarr(server, client)

    assert client.episode_calls == 2
