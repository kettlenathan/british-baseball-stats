"""Explicitly resets the local data/stats.db to the currently-published
GitHub Release snapshot, overwriting whatever's there. db/engine.py's
ensure_db_present() deliberately does NOT do this automatically once a
local file already exists (to avoid clobbering unpublished local
scrape/recompute work) — this script is the explicit opt-in for "throw away
my local copy and sync to the published one."

Usage:
    uv run python -m scripts.pull_latest_db
"""

from db.storage import download_latest


def main() -> None:
    if download_latest():
        print("Downloaded the latest published data/stats.db.")
    else:
        raise SystemExit("No published release found yet — run scripts.publish_db first.")


if __name__ == "__main__":
    main()
