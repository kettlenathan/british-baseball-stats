# British Baseball Stats Explorer

A Baseball-Reference/FanGraphs-style stats site for British baseball, built on data
scraped from [stats.britishbaseball.org.uk](https://stats.britishbaseball.org.uk), the
official stats platform for the British Baseball Federation.

It scrapes box scores, stores them in SQLite, derives sabermetric stats (wOBA, wRC+, FIP,
ERA+, a simplified WAR) from the raw play data, and serves it all through a Streamlit app.

## Features

- **Leaderboards** — league-wide batting/pitching leaders and team standings, filterable
  by league and season.
- **Player Explorer** / **Player Page** — browse and search all players; per-player career
  batting and pitching stat lines.
- **Team Page** — team-level season stats and roster.
- **Player Comparison** / **Team Comparison** — put 2+ players or teams side by side with
  trend charts across seasons.
- **Methodology** — explains exactly how wOBA, wRC+, FIP, ERA+, and WAR are calculated here,
  and where they diverge from official definitions (see [Methodology](#methodology--war-disclaimer)
  below).
- **Feedback** — report a bug or suggest a feature directly from the app; submissions are
  filed as GitHub issues on this repo.

A **Data Admin** page for triggering scraper runs also exists, but is only available when
running the app locally — it's excluded from the hosted deployment (see
[Deployment](#deployment)).

## Tech stack

- **Scraping**: `httpx` + a real HTML parser (not regex) to pull data out of the site's
  Inertia.js JSON payloads
- **Storage**: SQLite via SQLAlchemy, with Alembic migrations
- **Stats derivation**: plain Python, no ML — fixed sabermetric linear weights (Tom Tango
  et al.) combined with league-averages self-calibrated from this league's own data
- **App**: Streamlit
- **Package management**: [uv](https://docs.astral.sh/uv/)

## Getting started

```bash
uv sync                                  # install dependencies
uv run streamlit run app/Home.py         # launch the web app
```

The app needs data in `data/stats.db` to show anything. To populate it:

```bash
uv run python -m scripts.refresh_data --leagues nbl --years 2026
```

This scrapes the given league(s)/year(s) and computes all derived stats in one pass. It's
polite to the source site (a small federation server, not a CDN) — expect a few seconds per
request, plus longer pauses if the site temporarily rate-limits the scraper.

Run the test suite with `uv run pytest`, and lint with `uv run ruff check .`.

## How it's organized

```
scraper/    → discovers competitions/seasons and scrapes schedules + box scores
db/         → SQLAlchemy models (source of truth for schema) and the upsert layer
stats/      → derives season stats, league context, and WAR from the raw box scores
app/        → the Streamlit app that presents it all (read-only — never writes upstream)
```

Data flows one direction only: `scraper/` writes raw fact tables → `stats/` derives
sabermetric tables from those facts → `app/` displays what's already been derived. Every
ingestion write is idempotent (safe to re-run), and every derived table can be dropped and
rebuilt from the raw facts at any time.

Full architectural detail (page-by-page, module-by-module) is in `CLAUDE.md`.

## Methodology & WAR disclaimer

This league doesn't have play-by-play or batted-ball tracking data, so some things published
sabermetric sites take for granted aren't available here. In particular, **WAR in this app is
offense/pitching only — there is no defensive component at all**, and no park factors. It's
self-calibrated to this league's own actual run environment each season (so "0 WAR" means
league-average *here*, not relative to MLB), but it isn't directly comparable to official
bWAR/fWAR. The in-app **Methodology** page has the full breakdown of every formula used.

## Data refresh

Refreshing data is a manual, local process — there's no scheduled job. The recommended
cadence is weekly, e.g.:

```bash
uv run python -m scripts.refresh_data --leagues nbl,d2,d3,d4,d5 --years 2026
```

See `CLAUDE.md`'s "Data refresh cadence" section for the full reasoning (why weekly, why
Monday, cache behavior, etc.).

## Deployment

The hosted app (Streamlit Community Cloud) is a **read-only** consumer of a pre-built,
committed `data/stats.db` — it never scrapes live. Refreshing the deployed data means running
the refresh command above locally, then committing and pushing the updated `data/stats.db`.
See `CLAUDE.md`'s "Deployment" section for the full checklist and reasoning.

## Contributing feedback

Found a bug, a stat that looks wrong, or have a feature idea? Use the **Feedback** page in
the app itself — it files a GitHub issue on this repo directly.
