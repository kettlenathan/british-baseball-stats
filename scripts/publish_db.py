"""Publishes the local data/stats.db as the current GitHub Release asset —
the replacement for the old "git add data/stats.db && commit && push" step
(see CLAUDE.md's "Deployment" section). Requires a GITHUB_TOKEN environment
variable with `contents: write` on the repo (GitHub Actions provides one
automatically per run; locally, use a personal access token with that
scope).

Usage:
    export GITHUB_TOKEN=...
    uv run python -m scripts.publish_db
"""

import os

from db.storage import publish


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN environment variable is required to publish.")
    publish(token=token)
    print("Published data/stats.db to the GitHub Release.")


if __name__ == "__main__":
    main()
