# Scoping: AVG with runners in scoring position (RISP) and LOB

Not implemented yet — this is a design sketch to review before committing to
the work, per request. Nothing in this file changes runtime behavior.

## Where the data actually comes from

`scrape_boxscores.py` already fetches, per final game, the full Inertia
payload at `.../schedule-and-results/box-score/{game_id}` and only reads
`original["boxScore"]` out of it. The same payload has a sibling key,
`original["gamePlays"]["all"]`, that the site never surfaces in its own UI
box score view but is present in every response: a dict keyed by inning
number (`"1"`..`"7"`+), each value `{"top": [...], "bottom": [...]}`, each a
list of play-level records — one row per pitch/event, not one per game.

**Correction to what I told you before checking:** this does NOT require
re-fetching every historical game from the live site. Every one of the
4,111 `final` games in the DB already has its box-score response sitting in
`data/raw_cache/boxscore/*.json` (confirmed: file count == final-game count),
and `gamePlays` rides along in that same cached response. A backfill would
read from disk, not the network. Only new games going forward hit the network
at all, same as today.

## What a play record looks like (confirmed against a cached game)

```json
{
  "inning": 1, "home": 0, "outs": 1,
  "runner1": 0, "runner2": 0, "runner3": 0,
  "runner1after": 1020, "runner2after": 0, "runner3after": 0,
  "pa": 1, "ab": 1, "h": 1, "rbi": 0,
  "batterid": 407162, "pitcherid": 472610,
  "narrative": "MILNE Matthew singles to third base. Bunt. "
}
```

- `runner1`/`runner2`/`runner3`: base-runner state **before** this play (a
  nonzero value is a runner id-ish code, not a boolean — treat as "occupied"
  by checking `!= 0`).
- `runner1after`/`runner2after`/`runner3after`: base-runner state **after**.
- `pa`/`ab`/`h`/`rbi`: only meaningful on the record where a plate appearance
  actually concludes (most records in a PA's sequence — balls, strikes,
  foul-offs — carry `pa: 0`). Confirmed by inspecting every `pa == 1` record
  in a real game: `ab: 0` correctly on walks/HBP, `ab: 1` on balls in play and
  strikeouts.
- The **last record in a `top`/`bottom` list** is that half-inning's final
  play (3rd out, or a walk-off). Its `*after` fields are what's left on base
  when the half-inning ends.

## Proposed derivations

- **AVG w/ RISP**: filter to records where `ab == 1` and (`runner2 != 0` or
  `runner3 != 0`) *before* the play. Sum `h` / count of such records, per
  player (batter, keyed by `batterid` → the existing `Player.source_id`
  mapping) per game, then aggregate the same way season stats already are.
- **LOB**: for each `top`/`bottom` list, take the last record; count how many
  of `runner1after`/`runner2after`/`runner3after` are nonzero. That's LOB for
  that half-inning, attributable to the batting team (`home` flag tells you
  which). Sum across halves for a team's game LOB, then across games for a
  season total. This one is more of a state-machine walk than a simple
  filter — worth a unit test against a hand-verified game before trusting it.

## What this would take to build (rough shape, not estimated in hours)

1. A new parsing function (alongside `scrape_boxscore`, not replacing it)
   that reads `gamePlays` from the same already-parsed Inertia payload and
   extracts a compact per-PA row: `game_id`, `batter player_season_id`,
   `inning`, `home`, `ab`, `h`, `risp` (bool). Skip storing full pitch-level
   detail (balls/strikes/pitch coordinates) — none of that is used here.
2. A new fact table (e.g. `plate_appearances`) written alongside
   `batting_game_lines`, populated from the same box-score fetch — no new
   scrape step, just more of the existing one's payload being read.
2a. A per-half-inning LOB rollup — either computed on the fly from the same
    new table (if it also stores the "is this the half-inning's last PA"
    flag) or a second small table keyed by `game_id`/`team_season_id`.
3. Alembic migration for the above.
4. A backfill pass over `data/raw_cache/boxscore/*.json` (no network) plus
   the normal live path for new games going forward.
5. `stats/aggregation.py` additions to roll the new per-PA rows into season
   totals, and rate_stats helpers for `avg_risp` and `lob`.
6. Display: likely a small addition to the existing batting tables rather
   than a new page — matches how wOBA/wRC+ were bolted onto the existing
   batting table.

## Open questions before starting

- Batter identity on a play is `batterid` (site's player id) — confirm this
  is always resolvable to an existing `Player`/`PlayerSeason` row for every
  play (some plays might reference a pinch-runner or defensive sub not yet
  seen in `boxScore`'s own per-player records — unlikely but unverified).
- Whether `gamePlays` is present and shaped the same way across all
  historical seasons/leagues, or whether older seasons have a thinner/absent
  feed — only one cached game was inspected for this doc.
- LOB is normally a *team* stat in box scores, not a player stat — worth
  confirming that's the display grain wanted (vs. a per-player "runners left
  on base by this batter's outs," which is a different, less common stat).
