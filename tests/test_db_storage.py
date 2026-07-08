import json
from unittest.mock import patch

import pytest

import config
from db import storage


def _backdate_marker(db_path) -> None:
    marker = storage._read_marker(db_path)
    marker["checked_at"] -= 999999
    storage._marker_path(db_path).write_text(json.dumps(marker))


def test_marker_round_trip(tmp_path):
    db_path = tmp_path / "stats.db"
    storage._write_marker(db_path, updated_at="2026-01-01T00:00:00Z")

    marker = storage._read_marker(db_path)
    assert marker["updated_at"] == "2026-01-01T00:00:00Z"
    first_checked_at = marker["checked_at"]

    storage._touch_marker(db_path)
    touched = storage._read_marker(db_path)
    assert touched["updated_at"] == "2026-01-01T00:00:00Z"
    assert touched["checked_at"] >= first_checked_at


def test_read_marker_missing_file_returns_none(tmp_path):
    assert storage._read_marker(tmp_path / "stats.db") is None


def test_ensure_db_present_downloads_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stats.db")
    with patch.object(storage, "download_latest") as mock_download:
        storage.ensure_db_present()
    mock_download.assert_called_once_with(tmp_path / "stats.db")


def test_ensure_db_present_never_overwrites_existing_local_file_when_not_deployed(tmp_path, monkeypatch):
    db_path = tmp_path / "stats.db"
    db_path.write_bytes(b"local work in progress")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "is_deployed", lambda: False)

    with patch.object(storage, "download_latest") as mock_download, patch.object(storage, "_get_release") as mock_get:
        storage.ensure_db_present()

    mock_download.assert_not_called()
    mock_get.assert_not_called()
    assert db_path.read_bytes() == b"local work in progress"


def test_ensure_db_present_skips_network_when_deployed_and_marker_fresh(tmp_path, monkeypatch):
    db_path = tmp_path / "stats.db"
    db_path.write_bytes(b"data")
    storage._write_marker(db_path, updated_at="v1")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "is_deployed", lambda: True)

    with patch.object(storage, "_get_release") as mock_get, patch.object(storage, "download_latest") as mock_download:
        storage.ensure_db_present(max_age_hours=6.0)

    mock_get.assert_not_called()
    mock_download.assert_not_called()


def test_ensure_db_present_redownloads_when_deployed_and_release_changed(tmp_path, monkeypatch):
    db_path = tmp_path / "stats.db"
    db_path.write_bytes(b"data")
    storage._write_marker(db_path, updated_at="v1")
    _backdate_marker(db_path)  # force staleness regardless of max_age_hours

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "is_deployed", lambda: True)

    with (
        patch.object(storage, "_get_release", return_value={"assets": [{"name": storage.ASSET_NAME, "updated_at": "v2"}]}),
        patch.object(storage, "download_latest") as mock_download,
    ):
        storage.ensure_db_present()

    mock_download.assert_called_once_with(db_path)


def test_ensure_db_present_touches_marker_when_deployed_and_release_unchanged(tmp_path, monkeypatch):
    db_path = tmp_path / "stats.db"
    db_path.write_bytes(b"data")
    storage._write_marker(db_path, updated_at="v1")
    _backdate_marker(db_path)
    stale_checked_at = storage._read_marker(db_path)["checked_at"]

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "is_deployed", lambda: True)

    with (
        patch.object(storage, "_get_release", return_value={"assets": [{"name": storage.ASSET_NAME, "updated_at": "v1"}]}),
        patch.object(storage, "download_latest") as mock_download,
    ):
        storage.ensure_db_present()

    mock_download.assert_not_called()
    assert storage._read_marker(db_path)["checked_at"] > stale_checked_at


def test_publish_requires_token():
    with pytest.raises(ValueError):
        storage.publish(token=None)
