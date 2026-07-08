"""Publishes/fetches data/stats.db as a GitHub Release asset instead of a
git-tracked file.

Committing the ~50MB SQLite blob directly used to mean every weekly refresh
added a near-full copy to git history (SQLite files aren't diff-friendly),
growing .git by roughly the size of the whole database on every commit
regardless of how much data actually changed. This module replaces that
with a single, persistently-reused release (RELEASE_TAG) whose one asset
gets deleted and re-uploaded in place — there is no per-refresh versioning,
just "the current snapshot."

Downloads are unauthenticated (this repo is public, so release assets are
served over a plain HTTPS GET); publishing needs a token with `contents:
write` on the repo — a GITHUB_TOKEN environment variable (GitHub Actions
provides one automatically per run given `permissions: contents: write` in
the workflow; a local publish needs a personal access token with the same
scope, e.g. `export GITHUB_TOKEN=...` or a `gh auth token`).

Local-dev safety: ensure_db_present() only ever auto-downloads when
data/stats.db is completely missing (a fresh clone, or the deployed app's
ephemeral filesystem on cold start) — it will never silently overwrite an
existing local file just because time has passed, since that could clobber
a developer's own unpublished scrape/recompute work-in-progress. The
periodic "is there a newer published snapshot" re-check only runs when
config.is_deployed() is true, where there's no local work to protect and a
long-lived container would otherwise never see new data between redeploys.
"""

import json
import time
from pathlib import Path

import httpx

import config

RELEASE_TAG = "data-latest"
ASSET_NAME = "stats.db"
_API = "https://api.github.com"

# How long a deployed instance trusts its local copy before re-checking the
# release for a newer one — balances "don't serve stale data forever on a
# long-lived container" against "don't hit the GitHub API on every request."
STALE_AFTER_HOURS = 6.0


def _headers(token: str | None = None) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "british-baseball-stats"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_release(token: str | None = None) -> dict | None:
    response = httpx.get(
        f"{_API}/repos/{config.GITHUB_REPO}/releases/tags/{RELEASE_TAG}",
        headers=_headers(token),
        timeout=15.0,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def _find_asset(release: dict) -> dict | None:
    return next((a for a in release["assets"] if a["name"] == ASSET_NAME), None)


def _marker_path(db_path: Path) -> Path:
    return db_path.with_suffix(".release.json")


def _read_marker(db_path: Path) -> dict | None:
    marker_path = _marker_path(db_path)
    if not marker_path.exists():
        return None
    try:
        return json.loads(marker_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _write_marker(db_path: Path, updated_at: str) -> None:
    _marker_path(db_path).write_text(json.dumps({"updated_at": updated_at, "checked_at": time.time()}))


def _touch_marker(db_path: Path) -> None:
    marker_path = _marker_path(db_path)
    data = _read_marker(db_path) or {}
    data["checked_at"] = time.time()
    marker_path.write_text(json.dumps(data))


def download_latest(dest_path: Path | None = None) -> bool:
    """Downloads the current release asset to dest_path (default
    config.DB_PATH), overwriting whatever's there. Returns False (leaving
    any existing file untouched) if no release/asset has been published
    yet."""
    dest_path = dest_path or config.DB_PATH
    release = _get_release()
    if release is None:
        return False
    asset = _find_asset(release)
    if asset is None:
        return False

    with httpx.stream("GET", asset["browser_download_url"], follow_redirects=True, timeout=60.0) as response:
        response.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)
    _write_marker(dest_path, asset["updated_at"])
    return True


def ensure_db_present(max_age_hours: float = STALE_AFTER_HOURS) -> None:
    """Called once at db/engine.py import time. Downloads data/stats.db from
    the release if it's missing entirely; on a deployed instance, also
    re-checks for a newer published copy once the local file hasn't been
    verified in `max_age_hours`. See module docstring for why local (non-
    deployed) runs never auto-overwrite an existing file."""
    dest_path = config.DB_PATH
    if not dest_path.exists():
        download_latest(dest_path)
        return

    if not config.is_deployed():
        return

    marker = _read_marker(dest_path)
    if marker and (time.time() - marker["checked_at"]) < max_age_hours * 3600:
        return

    release = _get_release()
    if release is None:
        return
    asset = _find_asset(release)
    if asset is None:
        return

    if marker is None or marker.get("updated_at") != asset["updated_at"]:
        download_latest(dest_path)
    else:
        _touch_marker(dest_path)


def publish(file_path: Path | None = None, token: str | None = None) -> None:
    """Uploads file_path (default config.DB_PATH) as the release asset,
    creating the release on the very first publish. Requires a token with
    `contents: write` on the repo."""
    if not token:
        raise ValueError("publish() requires a token with contents:write on the repo")
    file_path = file_path or config.DB_PATH

    release = _get_release(token)
    if release is None:
        response = httpx.post(
            f"{_API}/repos/{config.GITHUB_REPO}/releases",
            headers=_headers(token),
            json={
                "tag_name": RELEASE_TAG,
                "name": "Latest data snapshot",
                "body": (
                    "Auto-published `data/stats.db` snapshot (see db/storage.py / "
                    "scripts/publish_db.py) — not a versioned software release, this "
                    "single asset is replaced in place on every refresh."
                ),
                "prerelease": True,
            },
            timeout=15.0,
        )
        response.raise_for_status()
        release = response.json()

    existing = _find_asset(release)
    if existing is not None:
        httpx.delete(
            f"{_API}/repos/{config.GITHUB_REPO}/releases/assets/{existing['id']}",
            headers=_headers(token),
            timeout=15.0,
        ).raise_for_status()

    upload_url = release["upload_url"].split("{")[0]
    with open(file_path, "rb") as f:
        response = httpx.post(
            upload_url,
            params={"name": ASSET_NAME},
            headers={**_headers(token), "Content-Type": "application/octet-stream"},
            content=f.read(),
            timeout=120.0,
        )
    response.raise_for_status()
