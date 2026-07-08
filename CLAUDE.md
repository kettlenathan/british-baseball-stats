# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

British Baseball Stats Explorer — scrapes `stats.britishbaseball.org.uk` (a WBSC-platform
site for the British Baseball Federation), stores results in SQLite, derives sabermetric
stats (wOBA, FIP, WAR, wRC+, ERA+) from the raw play data, and serves it all through a
Streamlit app in the style of Baseball-Reference/FanGraphs.

## Commands

Package management is `uv`; there is no separate venv-activation step needed for `uv run`.

```
uv sync                                  # install/sync dependencies
uv run pytest                            # run the full test suite
uv run pytest tests/test_stats_formulas.py::test_name   # run a single test
uv run ruff check .                      # lint
uv run streamlit run app/Home.py         # launch the web app

# Scraper + stats pipeline
uv run python -m scraper.pipeline --leagues nbl --years 2026
uv run python -m scraper.pipeline --leagues nbl,d2 --years 2024-2026 --force-refresh
uv run python -m stats.recompute --league-season-id N   # omit flag to recompute all
uv run python -m scripts.refresh_data --leagues nbl --years 2026   # scrape + recompute in one shot
uv run python -m scripts.refresh_data --leagues nbl --years 2026 --last-week   # mid-season: only recent games

# DB schema migrations (Alembic; models.py is the source of truth)
uv run alembic revision --autogenerate -m "..."
uv run alembic upgrade head
```

The Data Admin page in the Streamlit app (`app/pages/8_Data_Admin.py`) also runs
`scripts.refresh_data` as a subprocess, so scraper changes are exercised from the UI too —
this trigger is disabled when `IS_DEPLOYED` is set in `st.secrets` (see "Deployment" below).

## Architecture

**Pipeline, in dependency order:** `scraper/` → `db/` (raw fact tables) → `stats/` (derived
tables) → `app/` (read-only presentation). Data flows one direction only; nothing downstream
writes back upstream.

### `scraper/`
- `discovery.py` — finds which years a competition (`league_code`, e.g. `nbl`) has data for,
  via each competition's `/editions` page. Also holds `resolve_fetch_code(canonical_code,
  year)`: three of today's codes were renamed for 2026 (`d2` was `aaa`, `d3` was `aa`, `d4` was
  `a`, all through 2025 — confirmed against cached `/editions` responses and, for `d4`/`a`,
  cross-checked by team-name overlap against `d4`'s own 2026 roster, since hitting `d4`'s own
  `/editions` page directly mixes in unrelated regional competitions), so the canonical
  `League.code` stored in the DB can differ from the code actually used to build the URL for
  older years. `d5` is deliberately **not** mapped — its `/editions` page lists only 2026, i.e.
  no pre-2026 history exists at all. `scrape_schedule.py` and `scrape_boxscores.py` both call
  this to build their fetch URL while persisting under the canonical code.
- `scrape_schedule.py` — one fetch of a competition-year's `schedule-and-results` page yields
  the full season's games in one response (no pagination) — populates
  League/Season/LeagueSeason/Team/TeamSeason/Game.
- `scrape_boxscores.py` — one fetch per *final* game. Box score records carry batting,
  pitching, and fielding fields together on the same record (two-way players have both
  non-zero); records are grouped by `playerid` and summed before upserting, since
  substitutions produce multiple records per lineup spot for the same player. The same
  payload's `gamePlays.all` play-by-play feed is also walked to build one `PlateAppearance`
  row per plate appearance (batter/pitcher pair, first-pitch-strike, and batted-ball proxies)
  — several of this league's per-pitch fields are confirmed always zero and unusable
  (`ball`/`called`/`swing`/`foul`/`inplay`, and true `hitx`/`hity`/`exitvelo` coordinates), so
  first-pitch-strike is derived by diffing `balls`/`strikes` counts between pitches instead of
  reading a flag — see `_first_pitch_strike`'s docstring for the exact logic.
- `http_client.py` — plain `httpx` GETs work for everything (no headless browser needed at
  runtime); a browser `User-Agent` header is required or CloudFront 403s. Most pages embed
  data as an Inertia.js `data-page` JSON blob, extracted via a real HTML parser (not regex,
  since the JSON can contain `">` sequences).
- `rate_limiter.py` + `cache.py` — fixed delay+jitter throttle before every real network
  fetch; cache hits bypass the limiter entirely. Historical-season responses are cached with
  a ~10-year TTL (they never change); current-season responses use a 24h TTL.
- `pipeline.py` — CLI orchestrator (discovery → schedule → box scores) with a circuit
  breaker: after `CIRCUIT_BREAKER_THRESHOLD` (3) consecutive box-score failures it pauses
  `CIRCUIT_BREAKER_COOLDOWN_SECONDS` (180s), up to `CIRCUIT_BREAKER_MAX_TRIPS` (3) times per
  competition-year before giving up on the rest of that season for the run. This exists
  because sustained high-volume scraping was observed to trigger a cumulative
  rate/request-count block (100+ consecutive 403s) that doesn't clear on per-request retry
  but does clear after real wall-clock time — see `scraper/recon/findings.md` for the full
  writeup. Per-request `tenacity` retry in `http_client.py` is deliberately short (2
  attempts) since it only handles one-off blips, not sustained blocks.
- Design is idempotent/resumable throughout: every ingestion write goes through
  `db/upsert.py`, so any scrape step is always safe to re-run and a later run backfills
  whatever an earlier one gave up on.
- `scraper/recon/` is a one-off Playwright recon spike, not part of the runtime pipeline —
  `findings.md` there documents the site's URL structure, entity ID stability, and the box
  score data shape in detail; read it before changing scraping logic.

### `db/`
- `models.py` is the schema source of truth; schema changes should go through an Alembic
  migration (`db/migrations/versions/`), not just editing models and hand-editing the
  existing SQLite file. `db.engine.create_all()` (create-from-models) is for tests/dev only.
- Three layers of tables, in comments in `models.py`:
  1. **Dimensions** — League/Season/LeagueSeason (one league's instance in one year),
     Team/TeamSeason (a team's participation in one league_season — this is what the site's
     own `teamid` actually identifies), Player/PlayerSeason.
  2. **Facts** — Game, BattingGameLine, PitchingGameLine, PlateAppearance — written only by
     `scraper/`.
  3. **Derived/materialized** — BattingSeasonStats, PitchingSeasonStats,
     LeagueSeasonContext, BattingWar, PitchingWar, BatterSpraySeasonStats,
     BatterPitcherMatchup — written only by `stats/`, safe to drop and rebuild at any time
     from fact rows.
- Player identity: the site's `playerid` is confirmed stable platform-wide, so
  `players.source_id` uses it directly — no name-collision dedup needed. Team identity is
  *not* trusted to be stable across years (`teamid` is scoped per competition-instance), so
  cross-year `Team` identity is resolved by name matching in the upsert layer instead of a
  site-provided id.
- `PitchingGameLine.outs_recorded` stores outs, not float innings-pitched — this avoids the
  classic "1.1 + 1.1 = 2.2 IP" bug from summing baseball's IP notation as if it were decimal.
  Convert with `stats/rate_stats.py:outs_to_ip` for display/formulas.
- `upsert.py` is the one path all scraper/stats writes go through: insert-or-update keyed on
  a unique constraint, so every ingestion function is idempotent by construction.

### `stats/`
- `recompute.py` orchestrates the derivation pipeline in dependency order: aggregation
  (game lines → season totals, including pitchers' first-pitch-strike% rollup from
  `PlateAppearance`) → league context → batter spray tendency → batter/pitcher matchups →
  WAR. Never touches raw fact tables; safe to re-run any time after new games are scraped.
- `constants.py` — fixed sabermetric linear-weight coefficients (wOBA weights, FIP weights)
  from published research (Tom Tango et al.), treated as stable across run environments.
- `advanced_stats.py` — wOBA/wRC+/FIP/ERA+ formulas themselves, combining `constants.py`
  weights with a season stats row (wOBA, FIP) or a player rate stat plus its league-context
  counterpart (wRC+, ERA+). wRC+/ERA+ here are simplified vs. their real definitions: no park
  factor (fixed at neutral), since no park-factor data exists for this league.
- `league_context.py` — the self-calibration layer: league-average wOBA/OBP/SLG/ERA/FIP, the
  FIP additive constant (solved per league-season so lgFIP == lgERA that season), and the
  runs-per-win conversion (scaled from a 10-runs=1-win reference by this league's own actual
  runs/game) are all computed from this league's own scraped data, season by season — this
  is what makes WAR reflect this league's real environment rather than assuming MLB's. Pull
  tendency (below) deliberately does *not* follow this self-calibrated philosophy — a real
  ballpark's foul lines don't move with the league's own batted-ball distribution.
- `spray.py` — buckets each batter's batted balls into pull/center/oppo against **fixed**
  thirds of the true 90-degree fair-territory fan (+/-15 degrees off dead-center is "center",
  the outer 15-45 degrees on the batter's pull side is "pull", the same range on the other
  side is "oppo" — mirrored by handedness, matching `app/components/charts.py`'s
  `spray_heatmap` 9-bin fan) and labels their season tendency by whichever bucket holds a
  plurality. No longer depends on `league_context.py` — ordering between the two doesn't
  matter. `matchups.py` aggregates `PlateAppearance` rows into batter-vs-pitcher season
  totals (`BatterPitcherMatchup`), no minimum-PA filter, no ordering dependency. Both feed
  `app/pages/3_Player_Page.py`'s tendency/spray-chart/matchup sections; career values are
  summed across these season rows at read time in `app/components/data_access.py`, not
  stored separately.
- `war.py` — simplified batting/pitching WAR. **It is offense-only / FIP-only: there is no
  defensive component at all.** The box-score play-by-play does carry a coarse batted-ball
  proxy (pull direction, distance, ground/fly/line/pop type — `PlateAppearance`, used for
  spray charts and pull tendency above) but never true field coordinates, exit velocity, or
  fielder positioning data, so it can't support a real defensive metric; there are also no
  park factors. `WAR_DISCLAIMER` in this module is surfaced verbatim in the UI wherever WAR
  is shown — keep it accurate if the formula changes. `FORMULA_VERSION` in `constants.py`
  should be bumped if the formula changes, since it's stored alongside each computed WAR row.
- `shrinkage.py` — empirical-Bayes "true talent" shrinkage of season wOBA/FIP toward the
  league-season mean, weighted by PA/IP against a stabilization point (`k`) self-calibrated
  from this league-season's own player-to-player variance (method-of-moments over a
  Poisson-process approximation of within-player sampling noise), falling back to a published
  stabilization constant when a league-season's own data can't support the estimate (too few
  qualifying players, or the variance decomposition goes non-positive) — `k_self_calibrated`
  on each row records which path was used. Writes `BattingTrueTalent`/`PitchingTrueTalent`,
  one row per player-season, via the normal `stats/recompute.py` pipeline.
- `archetypes.py` — unsupervised k-means clustering of batters into descriptive archetypes,
  computed at read time (not materialized, unlike everything else in `stats/`) since it
  depends on user-adjustable parameters (population scope, k) with no single fixed "correct"
  value. Feature set is deliberately decorrelated: ISO, Center%, 1B%, and separate Pull%/Oppo%
  are all excluded in favor of a smaller set (Net Pull% = Pull% − Oppo%, BB%, K%, 2B%, 3B%,
  HR%) so no single underlying signal (e.g. power) is double-counted across multiple
  collinear/compositional features — see the module docstring for the full reasoning. k is
  chosen by maximizing mean silhouette score across a candidate range, not hardcoded; cluster
  labels and scatter-plot PCA axis labels are both generated from the same "top dominant
  features" logic (`_label_dominant_features`) rather than a fixed taxonomy or a bare
  "Component 1"/"Component 2". Batted-ball type (ground/fly/line/pop) is excluded entirely —
  see the `hittype` note above about that field's reliability.

### `app/`
- Streamlit multipage app; `app/Home.py` is the entry point, but page registration/order/
  visibility is explicit via `st.navigation()` in `Home.py` — not inferred from the
  `app/pages/N_*.py` filename prefixes (those numbers are just for humans browsing the
  directory; Streamlit no longer auto-discovers pages since the switch to `st.navigation`).
  Home's own dashboard content lives in a `render_home()` function passed to `st.Page()`
  alongside the rest, rather than as top-level script code.
- `app/env.py` holds `is_deployed()`, the one shared signal for "running on the hosted
  Community Cloud deployment vs. locally" — reads an `IS_DEPLOYED` secret that's only ever
  set via the Community Cloud dashboard, never committed. `Home.py` uses it to decide whether
  to include the Data Admin page in navigation at all; `8_Data_Admin.py` also checks it
  directly as defense in depth.
- `app/components/data_access.py` holds all `@st.cache_data`-wrapped DB-query functions
  returning pandas DataFrames. This layer only *displays* what `stats/` already derived —
  sabermetric formulas are never reimplemented or recomputed here, only read and formatted
  (e.g. `wrc_plus`/`era_plus` are computed inline from already-stored `woba`/`era` plus the
  league context row, but the underlying rate stats come from `stats/rate_stats.py`).
- `app/components/theme.py` is the one place chart colors are decided. `CATEGORICAL` (8 hues,
  light/dark) is the general palette; `OUTCOME_COLORS` fixes Home Run/Triple/Double/Single/Out
  to the same colors everywhere regardless of which subset a given player's data happens to
  include (not derived from `assign_colors`' "alphabetical among what's present" logic, which
  would otherwise let a color shift between charts); `TEAM_PALETTE` is a separate, bespoke
  10-hue set (color only — an earlier version also varied fill pattern/line dash/marker shape
  per team, which read as cluttered rather than professional) that `assign_colors()` draws
  from positionally for the `team` column, same as the general palette — deliberately *not*
  hashed to a stable per-name color (an earlier version was), since with only a handful of
  teams selected at once, positional assignment always hands them the most mutually-distinct
  colors available instead of scattering across all 10 slots by name; `filters.py`'s
  `team_multiselect` caps selection at 10 to match. `HEAT` is a dedicated red(most)/blue(least)
  scale used only by `spray_heatmap`
  — a deliberate exception to the usual one-hue sequential rule, per how that chart reads.
  `STAT_LABELS`/`stat_label()` is the single source of truth for every column header and
  chart label's display text (title-cased/proper abbreviation); `app/components/formatting.py`
  builds its `st.column_config` dicts from it rather than hardcoding labels a second time.
- `app/pages/4_Team_Page.py` shows one team's combined season stats (batting + pitching +
  fielding + situational, via `data_access.team_season_stats`) and its last 3 weekends of
  games (`data_access.team_recent_games` — "weekends" relative to that team's own most recent
  game in the selected league_season, not real wall-clock today, since historical seasons
  have no games near today) above the roster.
- `app/pages/5_Player_Comparison.py` and `app/pages/6_Team_Comparison.py` let a user pick
  2+ players or 1+ teams (via `filters.py`'s `player_multiselect`/`team_multiselect`) and see
  them side by side — batting and pitching career tables/trend charts for players, a
  win-pct-by-year trend for teams. Both reuse `charts.py`'s `trend_chart(..., color_col=...)`
  for the single-vs-multi-series overlay rather than branching in the page.
- `app/pages/7_Batter_Archetypes.py` fits `stats/archetypes.py`'s k-means clustering for one
  league-season, live at read time (via `data_access.batter_archetypes`, `@st.cache_data`
  wrapped so a repeat visit with the same scope/min-PA doesn't refit). Shows a PCA-projected
  scatter (axes labeled by dominant loading features, not "Component 1"/"2"), a silhouette-based
  "how k was chosen" diagnostics table, a cluster-profile summary table (deliberately broader
  than the clustering inputs — includes ISO/Center%/1B%/raw Pull%/Oppo% for descriptive context
  even though they aren't clustering features), and a static reference expander mapping each
  pairwise archetype dimension to a real, well-known MLB career.
- `app/pages/8_Data_Admin.py` runs the scraper/recompute pipeline as a subprocess from the
  UI and shows recent `ScrapeLog` activity. Only reachable at all when `is_deployed()` is
  False (see above); its own live-refresh controls are additionally gated the same way.
- `app/pages/9_Methodology.py` documents the wOBA/wRC+/FIP/ERA+/WAR formulas and what's
  fixed (published linear weight coefficients, `stats/constants.py`) vs. self-calibrated per
  league-season (`stats/league_context.py`), plus the fixed-geometry pull-tendency/spray-chart
  approximation, the first-pitch-strike% count-diffing method, the matchup table's
  no-minimum-sample-size caveat, the empirical-Bayes shrinkage formula (`stats/shrinkage.py`),
  and the batter-archetype clustering feature set/exclusions (`stats/archetypes.py`) — keep it
  in sync if any of those modules' approach changes.
- `app/pages/10_Feedback.py` files a GitHub issue against `config.GITHUB_FEEDBACK_REPO` via
  the REST API, authenticated with a `GITHUB_TOKEN` secret (Community Cloud dashboard or a
  local `.streamlit/secrets.toml` for testing — never committed). Degrades to an explanatory
  message if the secret isn't configured, rather than failing.
- Page order in `Home.py`'s `st.navigation()` list groups thematically rather than just
  following filename numbers: overview (Home) → league-wide stat views (Leaderboards, Player
  Explorer) → single-entity deep dives (Player Page, Team Page) → multi-entity analysis
  (Player Comparison, Team Comparison, Batter Archetypes) → ops (Data Admin, dev-only,
  spliced in via `pages.insert()`) → meta/reference (Methodology, Feedback). Filename number
  prefixes are kept in sync with this display order purely so the directory listing itself
  reads sensibly to a human browsing it — Streamlit itself only honors list order.

## Data refresh cadence

Refresh runs both on a schedule and on demand — three triggers, all ending at the same code
path (`scraper.pipeline.run()` then `stats.recompute.recompute_league_season()` per touched
`league_season_id`, via `scripts/refresh_data.py`):
- **`.github/workflows/main.yml`** — a GitHub Actions cron job, Sundays 21:00 UK time
  (`0 20 * * 0` UTC) plus manual `workflow_dispatch`. Runs `uv sync` then `scripts.refresh_data`
  windowed with `--last-week` (see below) for every league, commits the resulting `data/stats.db`
  as `github-actions`, and pushes straight to `main` — there's no PR/review step, so a bad scrape
  commits directly. It does **not** run `alembic upgrade head` first; it relies on the
  already-committed `data/stats.db` already carrying the current schema, so any schema-changing
  migration must be applied and committed locally *before* the next scheduled run (see
  "Deployment" below) — a run against the old pre-plate-appearances schema already produced one
  divergent auto-commit that had to be reconciled by hand when merging, and the same class of
  conflict can recur after a future migration.
- CLI: `uv run python -m scripts.refresh_data --leagues <codes> --years <spec>
  [--force-refresh] [--last-week | --last-month]`
- The Data Admin page's "Run refresh" button (`app/pages/8_Data_Admin.py`), which shells out
  to the identical command.

`config.CACHE_TTL_CURRENT_SEASON_HOURS = 24` means a same-day, non-forced re-run mostly hits
the raw-HTML cache rather than re-scraping the live site; historical seasons are cached
~forever. `--force-refresh` bypasses this.

The schedule fetch itself is always whole-season (one cheap request, needed to detect newly
final games), but box-score fetching — one request per final game, the actual cost driver for
a full season — can be windowed with `--last-week`/`--last-month`: `scraper/pipeline.py`'s
`run(..., since=...)` filters the box-score fetch list to games with `Game.game_date >= since`
after the schedule scrape has upserted `Game` rows, instead of re-checking every final game in
the season. Omitting both flags keeps the full-season behavior (needed once, e.g. after adding
new derived fields, to backfill every already-scraped game — the box-score JSON responses are
cached, so this reprocesses cached data rather than re-hitting the network).

**Recommended cadence: weekly** (games are played Sundays). `scrape_schedule()` only queues
box-score fetches for games already `status == "final"`, so a Sunday-night run (the scheduled
workflow's actual timing) can miss games the site hasn't finalized yet as of 21:00 UK — a
missed game is simply picked up by the following week's `--last-week` window instead, so this
is a one-week-late catch rather than a permanent gap:
```
uv run python -m scripts.refresh_data --leagues nbl,d2,d3,d4,d5 --years 2026 --last-week
```

## Deployment

The app is deployed to Streamlit Community Cloud as a **read-only** consumer of a committed,
pre-built `data/stats.db` — the live scraper never runs on the deployed instance (ephemeral
filesystem, no auth, blocking requests make that unsafe there). Data collection stays exactly
the process above, run locally; the only addition is committing the result:

1. `uv run alembic upgrade head` — ensure the DB about to be committed has the current schema.
2. `uv run python -m scripts.refresh_data --leagues nbl,d2,d3,d4,d5 --years 2026` — let it
   exit normally so SQLite's WAL checkpoints cleanly (an abrupt kill can leave `data/stats.db`
   mid-transaction relative to its `-wal` file, which `.gitignore` excludes from commits).
3. `git add data/stats.db` and commit/push — Community Cloud auto-redeploys on push.

`data/stats.db` is tracked in git (`.gitignore` un-ignores just that file; `data/raw_cache/`
and WAL sidecars stay ignored). Since SQLite files aren't diff-friendly, each commit stores a
full new blob — fine at the current ~10MB size, but reconsider (Git LFS or an external store)
if it grows past roughly 50-100MB.

Community Cloud installs from `requirements.txt` (it doesn't reliably support this repo's
PEP 621 `pyproject.toml`/`uv.lock` — it assumes Poetry format for `pyproject.toml` and doesn't
read `uv.lock` at all). Regenerate it after any dependency change:
```
uv lock
uv export --format requirements-txt --no-hashes --no-dev -o requirements.txt
```
`playwright` lives in the `recon` optional-dependency group (`uv sync --extra recon` to get
it locally) precisely so the default export above — and the deployed install — excludes it;
it's only used by the one-off spike in `scraper/recon/`, not the runtime pipeline or app.

The `IS_DEPLOYED` flag is set as a secret in the Community Cloud dashboard (never committed);
`app/env.py:is_deployed()` reads it to both drop the Data Admin page from navigation entirely
and (as defense in depth) gate its live-refresh controls if reached directly.

The Feedback page (`app/pages/10_Feedback.py`) needs a `GITHUB_TOKEN` secret (a PAT or
fine-grained token with Issues: write access on `config.GITHUB_FEEDBACK_REPO`) to file
submissions as GitHub issues — without it, the page shows a "not configured" message instead
of failing. Deliberately doesn't write feedback to `data/stats.db`: local file writes aren't
reliably persistent on Community Cloud (a reboot or redeploy can silently drop them), so
anything that needs to survive is sent to GitHub instead.

**Usage tracking** uses Community Cloud's own built-in analytics (viewer/visit counts, in the
app's dashboard under "Analytics") rather than anything built into the app — no in-app
tracking code exists or is needed for basic usage numbers.

## Testing

`tests/conftest.py` provides a `session` fixture backed by an in-memory SQLite engine with
`Base.metadata.create_all()` and `PRAGMA foreign_keys=ON` — tests build up rows directly via
the ORM/upsert layer rather than hitting the real scraped DB or the network.
