# Project Review — British Baseball Stats Explorer

A critical review of the codebase: code quality, the correctness of the analytical
insights (wOBA, wRC+, FIP, ERA+, WAR, shrinkage, archetypes), and where the project and
app could be improved. Every claim below was verified against the code; the test suite
(93 tests) passes, `ruff check .` currently fails (see C1), and the crash/edge cases
were reproduced empirically.

## Overall verdict

This is an unusually well-engineered project for its scope. Genuine strengths worth
naming explicitly:

- Clean one-directional pipeline layering (scraper → fact tables → derived tables →
  read-only app), enforced in practice, not just documented.
- Idempotent upserts as the single write path, making every scrape/recompute step
  safely re-runnable.
- `outs_recorded` stored instead of decimal IP, avoiding the classic IP-summing bug.
- Self-calibrated league context: the FIP constant solved per league-season, runs-per-win
  scaled to the league's own scoring — WAR is anchored to this league's environment
  rather than blindly assuming MLB's.
- Honest, prominent methodology documentation, including what is *not* possible with
  this data source.
- Careful scraping etiquette (rate limiting, response caching, a circuit breaker tuned
  from observed blocking behavior).
- A thoughtfully decorrelated feature set for the archetype clustering, with the
  reasoning written down.

The findings below are mostly about the gap between what the metrics claim to be and
what they compute, plus several latent correctness bugs.

---

## A. Sabermetric methodology findings

### A1. "wRC+" is not wRC+ — it materially understates player differences *(highest-impact finding)*

`stats/advanced_stats.py:37` computes `100 × (wOBA / lgwOBA)` — i.e. a "wOBA+". Real
wRC+ (even the park-neutral simplification) is:

```
wRC+ = 100 × ((wRAA/PA + lgR/PA) / (lgR/PA))
```

Because wOBA has a large baseline (~.320), the wOBA ratio compresses spread badly.
Example: with lgwOBA .320 and lgR/PA .125, a .384-wOBA batter shows wRC+ 120 here but is
really ~145 (wRAA/PA = .064 / 1.15 ≈ .056 runs/PA above a .125-runs/PA baseline). The
Methodology page's claim that "120 = 20% better than average" describes the runs-ratio
metric — exactly what the current implementation does not deliver.

Every input already exists: `BattingWar.wraa` and `LeagueSeasonContext.runs_per_pa` are
both stored. **Fix:** compute proper wRC+ in `stats/` (store it on `BattingWar`), update
the `app/components/data_access.py` call sites and the Methodology page.

### A2. Pitching WAR mixes ERA-scale and total-runs scales

`stats/war.py` computes runs above replacement from FIP (an *earned*-run, ERA-scale
stat) but divides by `runs_per_win` derived from *total* runs per game
(`league_context.py:_league_runs_per_game` sums final scores). fWAR bridges this by
multiplying FIP-scale quantities by lgRA9/lgERA (~1.08 in MLB). In an amateur league
with many errors, unearned runs are a much larger share of scoring, so pitching WAR is
systematically deflated relative to the batting side.

**Fix:** compute lgRA9 (`r` is already summed in `_league_pitching_totals`) and scale
`(lgFIP − FIP + repl)` by `lgRA9/lgERA`; bump `FORMULA_VERSION`.

### A3. Runs-per-win linear scaling is cruder than needed

`constants.py` scales 10 RPW linearly by RPG/4.5. The standard dynamic approximation
(Tango) is `RPW ≈ 1.5 × (runs per game, both teams) + 3` — no harder to compute from
data already in hand, and much better behaved in high-scoring environments (amateur ball
routinely runs 7+ R/G/team, where linear scaling overshoots). A cheap, published,
self-calibrating improvement.

### A4. ERA+ blanks out for a 0.00 ERA pitcher *(real bug)*

`advanced_stats.py:54` uses `if not player_era`, so a pitcher with, say, 10 shutout
innings gets ERA+ = None (blank cell) instead of the best value on the board — the falsy
check conflates "no ERA" with "0.00 ERA". Handle `era == 0` explicitly (capped display
value or a dedicated presentation). Worth auditing `wrc_plus` and the rest of
`rate_stats.py` for the same falsy-vs-None conflation.

### A5. Two fixed MLB constants deserve league-aware treatment

- **Replacement level** (20 runs/600 PA batting; 0.90 FIP runs/9 pitching) is an MLB
  convention. In a ~20-game amateur season with a much wider talent spread, true
  replacement level sits further from average, so WAR magnitudes are likely compressed.
  At minimum the Methodology page should flag replacement level as the least defensible
  borrowed constant; ideally estimate it from the league's own fringe population (e.g.
  bottom-decile-PA players' aggregate performance).
- **`WOBA_SCALE = 1.15`** varies with run environment; either self-calibrate it or
  document it as a known borrowed value alongside the linear weights.

### A6. `_league_runs_per_game` denominator includes score-less games

Final games whose scores are NULL are skipped by the SQL SUM but counted by COUNT,
slightly deflating runs/game and therefore runs/win. Filter on
`home_score`/`away_score` being non-null.

### A7. The shrinkage ("true talent") module is genuinely good

The method-of-moments stabilization estimate with a published fallback and a
`k_self_calibrated` provenance flag is the strongest analytical piece in the repo. One
refinement: `_estimate_stabilization` compares an unweighted `pvariance` of observed
rates against `V_e × mean(1/n)` — weighting each player's contribution by their own
`1/n` would stop small-sample players inflating the between-player variance estimate.

---

## B. Correctness bugs (verified)

### B1. The entire app layer keys players by `full_name`

`Player.full_name` is **not unique** (`db/models.py` — only `source_id` is). Every
career page, comparison, matchup table, spray chart, and true-talent lookup
(`data_access.py` throughout; `3_Player_Page.py` filters `player_tt["player"] ==
player`) silently merges any two distinct players who share a name — a realistic event
in a national federation (common names, fathers and sons). **Fix:** pass `player.id`
through the UI (selectbox over `(label, id)` pairs, labels disambiguated by team/birth
year) and key every query on id.

### B2. Batter Archetypes crashes for small populations *(reproduced)*

`fit_archetypes`/`select_k` raise `ValueError` for n=1 ("n_samples=1 should be >=
n_clusters=2") and n=2 (silhouette needs `n_labels <= n_samples − 1`). A short or new
league-season (e.g. d5 early season at min-PA 20) shows a raw traceback on the page.
Guard in `stats/archetypes.py` and show a friendly message in `7_Batter_Archetypes.py`.

### B3. Stale derived rows are never deleted

`stats/aggregation.py` `continue`s on zero totals instead of deleting an existing row,
and none of the WAR/matchup/spray/true-talent computations remove rows whose underlying
facts disappeared (a corrected box score, a re-scraped game). The models docstring says
derived tables are "safe to drop and rebuild", but `recompute.py` never drops. **Fix:**
in `recompute_league_season`, delete that league-season's derived rows first, then
rebuild, in one transaction — the cheap and fully correct version of the stated design.

### B4. The deployed staleness re-check never actually fires

`ensure_db_present()` runs once at `db/engine.py` import time, so the
`STALE_AFTER_HOURS` re-check only executes on process cold start — a long-lived deployed
container keeps serving the old file indefinitely, which is precisely the case the
mechanism was written for (Community Cloud hibernation masks this in practice, but the
documented behavior doesn't exist). Even if it did fire, the already-created engine's
connection pool holds the old file and `st.cache_data` is never invalidated. **Fix:**
move the staleness check into the app entry point (`Home.py`), and on re-download call
`engine.dispose()` + `st.cache_data.clear()` — or accept restart-based refresh and
document that instead.

### B5. `st.cache_data` has no TTL anywhere

A locally running app never sees data refreshed via the CLI
(`scripts.refresh_data`) until restart — only the Data Admin button path clears the
cache. A modest `ttl=3600`, or a cache-buster argument keyed on
`max(ScrapeLog.fetched_at)`, fixes this and half of B4.

### B6. WAR disclaimer coverage contradicts CLAUDE.md

CLAUDE.md says the disclaimer is surfaced "wherever WAR is shown", but it appears only
on Home, Leaderboards, and Methodology. Player Page, Player Explorer, Player Comparison,
and Team Comparison all display WAR (columns, trend charts, radar spokes) with no
caption. Add the caption there, or soften the CLAUDE.md claim.

---

## C. Engineering & process

### C1. No CI for tests/lint — and lint is currently red

The only workflow is the data refresh. `ruff check .` fails today with 3 × E402 in
`db/migrations/env.py` (the imports are deliberately placed after `fileConfig`; add
`# noqa: E402` or a per-file ignore in `pyproject.toml`). Add a small CI workflow:
`uv sync`, `ruff check .`, `pytest`.

### C2. Test collection hits the network *(reproduced)*

Importing `db.engine` (transitively via `app.components.data_access` in
`tests/test_data_access.py`) executes `ensure_db_present()` — a GitHub download — at
*import time*. On a fresh clone without `data/stats.db`, pytest collection errors before
a single test runs. **Fix:** make `ensure_db_present()` lazy (first `get_session()` /
first connect) or skip it under pytest; the test fixture already builds its own
in-memory engine and never needs the real one.

### C3. The weekly refresh workflow publishes unconditionally

`scripts/refresh_data.py` swallows all scrape failures (prints and continues) and always
exits 0; the workflow then publishes. A structurally broken scrape (site redesign →
every box score fails) would still overwrite the published snapshot while the Sunday
cron reports green. **Fix:** exit non-zero (or skip the publish step) when permanent
failures exceed a threshold, and add a cheap pre-publish sanity check (row counts not
lower than the pulled snapshot's).

### C4. Minor

- N+1 queries: one SUM per player-season in `aggregation.py`, four queries per team in
  `team_season_stats` — replace with `GROUP BY`; harmless at current scale.
- `sys.path.insert` boilerplate atop every page — installing the project as a package
  removes it.
- The Feedback page lets any anonymous visitor file GitHub issues using the hosted
  token; add a simple per-session rate limit before this gets discovered.

---

## D. App functionality improvements (ranked by value)

1. **Game logs and box-score pages.** `BattingGameLine`/`PitchingGameLine`/`Game`
   already hold everything needed for per-player game logs and a per-game box score
   view — the single biggest Baseball-Reference-style feature missing, at zero new
   scraping cost.
2. **Richer standings**: RS/RA, run differential, Pythagorean expectation, games
   behind, last-10/streak — `standings()` currently shows only W/L/T/pct.
3. **Career/all-time leaderboards** across seasons — leaderboards are currently
   single-league-season only; the career-combining helpers in `data_access.py` already
   do the hard part.
4. **Cross-league trend lines in context**: Player Page/Comparison trend charts plot
   raw OPS/ERA across years that may span different divisions; wRC+/ERA+ (after A1) are
   the right series, and `trend_chart` already supports a `reference_y=100`
   league-average line (currently unused on those pages).
5. **Qualification defaults tied to schedule length** (e.g. 2.7 PA per team game,
   1 IP per team game) rather than fixed slider defaults.
6. **Surface WAR components** (wRAA, replacement runs, runs/win used) on the Player
   Page — `BattingWar.wraa` is already stored; this makes the simplified WAR auditable.
7. **Data-quality panel** on Data Admin: final games missing play-by-play
   (`home_lob IS NULL`), permanently failed box scores, per-league last-scraped — mostly
   derivable from `ScrapeLog` + `Game` today.
8. **Player disambiguation UI** (comes along with fix B1).

---

## Recommended priority order

1. A1 (wRC+ formula) + A4 (ERA+ zero bug) + Methodology text — the headline insight
   corrections.
2. B1 (player identity by id) — correctness of every player view.
3. B2 (archetypes crash guard), B3 (delete-then-rebuild derived tables).
4. A2/A3 (pitching WAR RA9 bridge, dynamic runs-per-win) + `FORMULA_VERSION` bump.
5. B4/B5 (cache/staleness), B6 (disclaimer coverage).
6. C1 (CI + ruff fix), C2 (hermetic tests), C3 (publish guard).
7. D-list features, starting with game logs.
