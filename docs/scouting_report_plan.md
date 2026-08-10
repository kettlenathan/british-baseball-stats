# Plan: Opponent Scouting Reports + Lineup Optimizer

Status: **design approved-pending-review — not yet implemented.**
Target branch: `claude/scouting-reports-lineup-ebatuc`.

## Goal

A new "Scouting Report" page that, for a chosen opponent (defaulting to your team's next
scheduled fixture), generates a downloadable **PDF** containing:

1. An opponent overview (record, standings position, recent form, team batting/pitching vs
   league average).
2. Their best players, ranked on shrunk "true talent" values so small samples don't mislead.
3. Spray charts for their key hitters — this season **and** career — plus pull/center/oppo
   tendency labels and vs-hand notes.
4. A "probable pitchers" section: who is likely to start against you, inferred from their
   staff's actual usage history, with a full breakdown of each likely pitcher (K%/BB%/FIP,
   first-pitch-strike%, spray of contact allowed, and **your batters' head-to-head history
   against them** from `BatterPitcherMatchup`).
5. A recommended batting order for your own team, from the players you mark available that
   week, optimized against the probable starter's handedness.

The PDF is the deliverable (easy to scroll on a phone in a dugout, printable, shareable with
the team); the page itself shows a lighter interactive preview of the probable pitchers and
recommended lineup.

## What already exists that this builds on

Almost everything needed is already in the DB or the read layer — this feature is
overwhelmingly *assembly*, not new data collection:

| Need | Already exists |
|---|---|
| Next opponent detection | `Game` rows are upserted for the whole season including `status == "scheduled"` fixtures (`scraper/scrape_schedule.py`) — filter to your team's future games |
| Opponent record / form | `data_access.standings()`, `team_recent_games()`, `team_season_stats()` |
| Best players (small-sample-safe) | `BattingTrueTalent` / `PitchingTrueTalent` (`stats/shrinkage.py`), surfaced via `batting_true_talent()` / `pitching_true_talent()` |
| Spray charts, season + career + vs-hand | `data_access.batter_spray_points(full_name, league_season_id=None → career, vs_hand=...)` and `pitcher_spray_points(...)` |
| Pull tendency labels | `data_access.batter_tendency()` / `BatterSpraySeasonStats` |
| Who started each game | `PlateAppearance` has `game_id` + `inning` + `half` + `pitcher_player_season_id` — the pitcher of a team's first defensive PA of a game *is* the starter |
| Your batters vs their pitchers | `BatterPitcherMatchup` (materialized), via `batter_pitcher_matchups_*()` |
| Handedness | `Player.bats` / `Player.throws`, populated from box-score payloads for anyone who has appeared (nullable — degrade gracefully) |
| Rosters | `data_access.team_roster()` + `PlayerSeason.position_primary` |

**No schema change, no Alembic migration, no scraper change.** All new derivation is
read-time, following the `stats/archetypes.py` precedent (parameterized by user choices —
opponent, available players — so materializing would be wrong anyway).

## Key design decisions

### PDF stack: `reportlab` + `matplotlib` (not plotly + kaleido)

The deployed app on Streamlit Community Cloud must be able to generate these PDFs (it's a
read-only operation, so unlike Data Admin there is no reason to gate it to local runs).
Plotly static-image export requires kaleido, which as of v1 requires a Chrome binary on the
host — fragile on Community Cloud (would need `packages.txt` + env plumbing) and heavy.
Instead:

- **`reportlab`** (pure-Python, wheels everywhere) builds the document via Platypus flowables
  (tables, headings, page breaks, page numbers).
- **`matplotlib`** (`Agg` backend, no display needed) re-renders the spray fan as PNGs
  embedded in the PDF. The fan geometry must mirror `app/components/charts.py`
  (`_pull_to_theta`, the fixed ±45° fair-territory fan, 9-bin heatmap) — extract the shared
  geometry constants into a small helper if needed rather than duplicating magic numbers.
- Colors come from `app/components/theme.py` (`OUTCOME_COLORS`, light mode) so the PDF
  matches the app. Light mode only — PDFs are print-first.

Both are plain pip deps → `uv add reportlab matplotlib`, then regenerate `requirements.txt`
per the documented export command so Community Cloud picks them up.

### Where code lives (respecting the one-way pipeline)

- `stats/probable_pitchers.py` — starter identification + likelihood ranking (pure logic on
  ORM rows, testable against the in-memory fixture DB).
- `stats/lineup.py` — batter event profiles, Markov run-expectancy model, order optimizer
  (pure, no Streamlit).
- `app/components/data_access.py` — new `@st.cache_data` wrappers: `next_fixtures(team, ls_id)`,
  `probable_pitchers(team, ls_id)`, `lineup_inputs(team, ls_id, names, vs_hand)`,
  `team_batting_true_talent_for_team(...)` etc. Queries stay here; math stays in `stats/`.
- `app/components/scouting_pdf.py` — the reportlab document builder + matplotlib chart
  renderers. Presentation-only: consumes DataFrames from `data_access`, never opens a DB
  session itself.
- `app/pages/8_Scouting_Report.py` — the page. See renumbering note below.

### Page placement and renumbering

Display order: after Batter Archetypes (it closes the "analysis" group as the most applied,
game-prep page), before Data Admin. CLAUDE.md's convention is that filename number prefixes
track display order, so renumber via `git mv`:
`8_Data_Admin.py → 9_...`, `9_Methodology.py → 10_...`, `10_Feedback.py → 11_...`, new page
becomes `8_Scouting_Report.py`. Update the `st.Page` paths and the `pages.insert(8, ...)`
index in `app/Home.py` (Data Admin's insert position stays 8 → becomes 9? — recount at
implementation time; the insert index is positional in the list, not the filename number).
Grep for any other references to those filenames (tests, docs) before renaming.

## Feature detail

### A. Page UX flow (`8_Scouting_Report.py`)

1. **League/season** — existing `filters.league_season_selector()`.
2. **Your team** — selectbox over that season's teams, persisted in `st.session_state`
   (optionally seeded from a `MY_TEAM` var in `.env` via `config.py`; nice-to-have, not v1-blocking).
3. **Opponent** — selectbox defaulting to the opponent of your team's next
   `status == "scheduled"` game (show the fixture date/venue as a caption). Free choice so
   you can prep for any team, or use it in historical seasons where no future games exist.
4. **Available players** — multiselect over your roster, default = everyone; **lineup
   length** radio: 9 or 10 (extra hitter is common in BBF play).
5. **Probable starter override** — the inferred ranking is a heuristic; let the user pick
   "actually, we know it's X" from the opponent's staff, which re-runs the vs-hand lineup
   adjustment.
6. Inline preview: probable pitchers table + recommended lineup card.
7. **"Generate PDF"** button → builds in memory (`io.BytesIO`), `st.download_button`
   ("Scouting Report — {opponent} — {date}.pdf"). Show `st.spinner`/progress while the
   matplotlib charts render (expect a few seconds).

### B. PDF contents

1. **Cover / header** — "{Your team} vs {Opponent}", fixture date + venue if a scheduled
   game exists, league-season, generated-on date, data-freshness (`ScrapeLog` max
   `fetched_at`).
2. **Opponent overview** — W-L, standings position, run diff, last 3 weekends' results
   (`team_recent_games`), team batting (AVG/OBP/SLG/wOBA) and pitching (ERA/FIP/K%/BB%)
   side-by-side with league average from `LeagueSeasonContext`.
3. **Their hitters** — full-roster table ranked by shrunk wOBA (columns: PA, AVG/OBP/SLG,
   wOBA, shrunk wOBA, wRC+, SB, K%, BB%, tendency label, bats). Then a per-player detail
   block for the **top N (default 6) by shrunk wOBA with ≥ 20 PA**: season spray chart and
   career spray chart side by side, tendency label, one-line auto-generated note ("pull-heavy
   ground-ball hitter; has struck out in 31% of PAs vs RHP"). Batters without batted-ball
   data (no play-by-play seasons) get the table row but no chart, with a "no batted-ball
   data" note.
4. **Their pitching staff & probable pitchers** — see C. Per likely pitcher (top 3): season
   + career line (IP, ERA, FIP, K%, BB%, FPS%), throws, spray-against chart, vs-LHB/vs-RHB
   opponent line, and a table of **your available batters' career history vs them**
   (`batter_pitcher_matchups_career`, with the existing no-minimum-sample caveat printed).
5. **Recommended lineup** — see D. The order, expected runs/game, comparison vs two
   baselines (descending shrunk wOBA; the order the user's multiselect happened to be in),
   and per-slot rationale sentences.
6. **Methodology & caveats appendix** — one page: probable pitchers are a usage-based
   heuristic, matchup samples are tiny, spray geometry is the fixed-fan approximation,
   `WAR_DISCLAIMER` verbatim where WAR appears, lineup model assumptions.

### C. Probable pitchers (`stats/probable_pitchers.py`)

1. **Starter per game**: for each final game of the opponent's season, the starter is the
   pitcher of their first defensive plate appearance (min `(inning, PA id)` among
   `PlateAppearance` rows for the right `half` given home/away). Games with no play-by-play:
   fall back to the game's `PitchingGameLine` with the most `outs_recorded` (flagged
   lower-confidence).
2. **Usage table** (whole staff): G, GS, IP, share of team IP, ERA/FIP, last appearance
   date, IP by weekend for the last ~4 weekends (a compact grid — coaches can eyeball
   patterns like strict two-man rotations instantly).
3. **Likelihood ranking**: recency-weighted start share (exponential decay over weekends,
   half-life ≈ 2 weekends), boosted if the team alternates starters and this pitcher did
   *not* start last weekend, damped if the pitcher already threw a large IP load last
   weekend. Output = ranked list with a qualitative confidence (High/Medium/Low) and the
   evidence ("started 5 of last 6 Sundays"), **never** a hard prediction. Doubleheaders
   (two games same date) are the norm — predict a top-2, not a single starter.
4. No pitch-count/rest-rule modeling in v1 (BBF rules vary by level; a config knob later if
   wanted).

### D. Lineup optimizer (`stats/lineup.py`)

1. **Batter event profile**: from season counting stats, per-PA probabilities of
   {BB+HBP, 1B, 2B, 3B, HR, out}. Small-sample handling: scale the hit-event probabilities
   so the profile-implied wOBA equals the player's **shrunk** wOBA from
   `BattingTrueTalent` (walk rate shrunk separately toward league BB% with the same
   PA-vs-k weighting). Players with no season data (didn't qualify for shrinkage) get the
   league-average profile.
2. **Vs-hand adjustment** (only when the probable/overridden starter's `throws` is known):
   compute each batter's career vs-that-hand wOBA from `PlateAppearance`, shrink it toward
   their own overall shrunk wOBA (platoon splits stabilize very slowly — heavy shrinkage,
   k on the order of several hundred PA), and rescale the profile.
3. **Order evaluation**: expected runs per game from a standard Markov chain over the 24
   base-out states with fixed advancement rules (single: runner on 2nd scores, runner on
   1st → 2nd; double: runners on 2nd/3rd score, 1st → 3rd; etc.), 7-inning games (BBF
   norm — make innings a parameter), cycling through the 9- or 10-man order.
4. **Optimization**: seed with a "The Book"-style heuristic slotting (best OBP → 1–2, best
   overall → 2/4, power → 4–5, weakest → 8–9), then hill-climb with pairwise swaps under
   the Markov evaluator until no swap improves, with a few random restarts (deterministic
   seed so the same inputs always give the same lineup). Exhaustive 9!/10! search is
   unnecessary; document that the gap between good orders is small (~a few runs per
   *season*) so the rationale text matters as much as the ordering.
5. Positions/fielding are **out of scope** — the tool orders hitters; the coach assigns
   positions. Say so in the UI.

## Dependencies

```
uv add reportlab matplotlib
uv lock
uv export --format requirements-txt --no-hashes --no-dev -o requirements.txt
```

Both are wheel-only installs, safe for Community Cloud. Neither belongs in the `recon`
extra — they're runtime app deps.

## Testing (`tests/`, against the in-memory fixture DB)

- `test_probable_pitchers.py` — starter identified from PA data; fallback to max-outs line
  when no play-by-play; recency weighting ranks the recent starter first; doubleheader
  alternation case.
- `test_lineup.py` — Markov evaluator sanity: team of league-average hitters ≈ league
  runs/game; a strictly better hitter raised expected runs; optimizer output is
  deterministic; degenerate inputs (a batter with 0 PA, all-identical batters) don't crash;
  10-man order cycles correctly.
- `test_scouting_pdf.py` — smoke test: build the full PDF from fixture rows, assert it's
  non-empty, starts with `%PDF`, and contains the expected section count; graceful-degradation
  cases (opponent with no play-by-play data, players with null `bats`/`throws`, no scheduled
  fixture).
- Extend `test_data_access.py` for the new query wrappers.

## Implementation order

1. `stats/probable_pitchers.py` + tests.
2. `stats/lineup.py` + tests.
3. `data_access` wrappers + tests.
4. `app/components/scouting_pdf.py` (charts first, then document assembly) + smoke tests.
5. Page + `Home.py` navigation + filename renumbering.
6. Deps + `requirements.txt` regen; `9_Methodology.py` (→`10_`) additions: probable-pitcher
   heuristic, lineup model + shrinkage reuse, platoon-shrinkage choice; CLAUDE.md updates
   (new modules, renumbered pages).
7. `uv run pytest`, `uv run ruff check .`, manual run-through in the app.

## Defaults chosen (flag if you want different)

- **PDF works on the deployed app too** (read-only, so safe) — not gated like Data Admin.
- **Top 6 opposing hitters** get full spray-chart blocks (whole roster still tabled);
  spray charts rendered as scatter fans (season) — heatmap variant only if a hitter has 25+
  balls in play, else points-only.
- **7-inning** run model default, parameterized.
- **Lineup length 9 or 10**, user radio, default 9.
- Optional `MY_TEAM` in `.env` to pre-select your team; otherwise session-state remembers it.
- Career spray = all seasons summed (matching the Player Page's read-time career convention).
