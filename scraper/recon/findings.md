# Recon findings: stats.britishbaseball.org.uk

Source: Playwright network capture of `/en/calendar`, followed by manual `httpx`
probing of discovered URL patterns. Raw captures kept in `scraper/recon/output/`
(gitignored — regenerate with `uv run python -m scraper.recon.explore_calendar`
plus the ad-hoc probes described below) for reference during scraper development.

## Platform

This is a WBSC-wide platform (World Baseball Softball Confederation) that many
national federations run on — the British Baseball Federation (BBF) is
**`federation_id = 20`** in this platform. The site mixes two rendering styles:

1. **Inertia.js pages** (Laravel + Vue, ship a full JSON prop tree in
   `<div id="app" data-page="...">` on every page load — no separate XHR
   needed, no need to reverse-engineer a REST API). Used by:
   - `/en/calendar` and `/en/calendar/{year}` — component `HomePage/index`
   - `/en/events/{comp}/schedule-and-results` — component `ScheduleAndResults/index.tsx`,
     `props.games` is the full list of games for that competition instance (all games in
     one response, no pagination hit in testing — 108 games for a full 2026 NBL season)
   - `/en/events/{comp}/schedule-and-results/box-score/{game_id}` — component
     `BoxScore/index`, `props.viewData.original` has `tournamentInfo`, `gameData`,
     `boxScore`, `gamePlays`

2. **Plain server-rendered HTML tables** (classic Blade, no JS needed to read
   them, just parse `<table>` elements):
   - `/en/events/{comp}/standings` — one `<table class="standings-print">`,
     rows have team name + link to `/en/events/{comp}/teams/{team_id}`, W/L/T/PCT/GB
   - `/en/events/{comp}/teams` — team list/links
   - `/en/events/{comp}/stats?section=leaders` — stat leader tables
   - `/en/events/{comp}/teams/{team_id}/players/{player_id}` — individual player page
   - `/en/events/{comp}/editions` — links to the same competition in other years
     (confirmed NBL has editions back to 2021: `2021-nbl` ... `2026-nbl`)
   - `/en/events/{comp}/home` — landing page, also links to every team's roster
     (`.../teams/{team_id}/players/{player_id}`) and every played game's box score

**Bottom line: no headless browser is needed for the actual scraper.** Plain
`httpx` GETs work for every page type above. Playwright was only needed for
this recon spike (to see what JS requests fired) — the real content turned
out to be server-rendered either way.

## Update (Milestone 7, historical scrape): 403s under sustained load

During the first multi-season historical scrape (~190 sequential requests)
one isolated box-score request got a 403 that cleared on immediate retry —
looked like a one-off blip. But scraping several full seasons back-to-back
in one sitting (multiple pipeline runs in a row, low thousands of requests
total) later produced a **sustained run of 100+ consecutive 403s** on a
division scrape, which did NOT clear on individual retry within the same
run, yet cleared within under a minute of stopping the process. This looks
like a cumulative rate/request-count threshold (requests-per-minute-or-hour
budget) rather than a purely per-request block — plausible for a small
federation's backend, not a CDN built for scraping load.

Two changes as a result:
- `config.REQUEST_DELAY_SECONDS` raised from 1.5s to 3.0s (jitter 0.5→1.0s)
  to reduce sustained load in the first place.
- `scraper/pipeline.py` now has a **circuit breaker**: after
  `CIRCUIT_BREAKER_THRESHOLD` (3) consecutive box-score failures within one
  competition-year, it pauses `CIRCUIT_BREAKER_COOLDOWN_SECONDS` (180s)
  before continuing, up to `CIRCUIT_BREAKER_MAX_TRIPS` (3) times per
  competition-year before giving up on the remainder of that season for the
  run (later re-runs will pick up the missing games — the design is
  idempotent/resumable by construction). `scraper/http_client.py`'s
  per-request `tenacity` retry was deliberately shortened (2 attempts,
  short backoff) in favor of this — burning 30s of backoff per request
  during a sustained block just delays hitting the same wall on the next
  request too; the circuit breaker handles the sustained case, per-request
  retry handles genuine one-off blips.
- If a big scrape run ever needs to be repeated because of this, prefer
  running it standalone rather than back-to-back with other scrape runs in
  the same session, and expect it may take one extra re-run pass to
  backfill whatever the circuit breaker gave up on.

## The one gotcha: CloudFront blocks default HTTP clients

A plain `httpx.get(url)` with no headers gets a **403 from CloudFront**
("Request blocked"). Setting a normal browser `User-Agent` header is
sufficient to pass:

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
```

No cookies/session/auth needed beyond that. `scraper/api_client.py` (or
whatever the HTTP client module ends up being called) must always send this
header. Rate limiting (per the plan: ~1-2s delay + jitter) should still be
applied even though there's no auth wall — this is a small federation's
server, not a CDN built for scraping load, and CloudFront may start blocking
harder if hit too fast.

## URL / competition structure

Pattern: `/en/events/{year}-{competition_code}/{page}`

Competition codes discovered for 2026 (from server-rendered links on
`/en/calendar/2026`, no JS needed): **`nbl`, `d2`, `d3`, `d4`, `d5`, `dev`,
`friendlies`**. `nbl` = National Baseball League (the top senior tier,
`category: "BBF-NBL"` in the tournament data), `d2`-`d5` = Division 2-5 (lower
senior tiers). No separate women's baseball competition code was found in the
2026 calendar — this federation currently appears to run men's baseball only
at the senior level (translations for `baseball-w`/`softball-*` exist in the
platform generally but no corresponding live BBF competition was found; worth
a quick re-check each season in case that changes).

`dev` (development) and `friendlies` (exhibition games) are structurally
identical competitions in the data model but arguably don't fit "senior adult
league" in spirit. Recommendation: include `nbl` + `d2`-`d5` as the core
senior scope for the MVP (Milestone 3/4), decide on `dev`/`friendlies`
inclusion later once their actual content/rosters can be inspected (Milestone
7 scope-expansion step) — not worth blocking on now.

Historical seasons: each competition's `/editions` page links to the same
competition code prefixed with other years (`2021-nbl` through `2026-nbl`
confirmed). Discovery flow: hit `/en/calendar/{year}` for descending years
starting from the current year to find each year's active competition codes
(codes can change or be added/dropped year to year — don't assume the 2026
set is exhaustive for prior years), **and/or** hit `/en/events/{current}-{code}/editions`
for each known code to get its full historical year list directly. The
`editions` page approach is more direct and is the recommended primary
discovery mechanism per competition; the `/en/calendar/{year}` approach is a
useful cross-check / way to discover competition codes that may not exist
yet in the current year's set (e.g. a discontinued division).

## Entity ID stability — player identity is a non-issue

Every player has a **stable platform-wide numeric `playerid`** (e.g. `739750`),
present in every batting/pitching line, distinct from `teamid` (e.g. `41854`,
also stable per team-per-competition-instance) and `tournamentid` (e.g. `3514`,
one per competition-year instance). The nested `player` object on every box
score line includes `dob` (year only, e.g. `"1997"`), `bats`, `throws`,
`nationality`. **This resolves the biggest open risk flagged in the plan** —
no name-collision dedup heuristic is needed, `players.source_id = playerid`
is a clean, reliable key.

Team identity: `teamid` appears stable within a competition instance; not yet
confirmed whether the same physical team keeps the same `teamid` across
different years/competitions (e.g. Croydon Pirates in 2025 vs 2026 NBL) — spot
check this during Milestone 3 by comparing team lists across two seasons of
the same competition. If `teamid` changes year to year, fall back to matching
`teams` by name (already planned for in the schema's `teams` vs `team_seasons`
split).

## Box score data shape (the core fact table source)

`boxScore` in the Inertia payload is a dict keyed by `teamid` (two keys per
game, home + away), each value a dict keyed by batting-order spot (`"1"`
through `"9"`, plus `"90"` for players who only pitched / didn't bat in the
lineup) → list of player-appearance records (list because of substitutions
mid-game). **Each record has both batting fields (`pa`, `ab`, `r`, `h`,
`double`, `triple`, `hr`, `rbi`, `bb`, `so`, `sb`, `cs`, `hbp`, `sf`, `sh`,
`gdp`, `ibb`) AND pitching fields (`pitch_ip`, `pitch_h`, `pitch_r`,
`pitch_er`, `pitch_bb`, `pitch_so`, `pitch_hr`, `pitch_bf`, `pitch_win`,
`pitch_loss`, `pitch_save`, etc.) AND fielding fields (`field_po`, `field_a`,
`field_e`, `field_dp`, `field_sba`, `field_csb`, `field_pb`) on the same
record** — a two-way player has both non-zero. The scraper's job per box
score is: flatten all team/spot buckets into one list of player-appearance
records, then split each into a `batting_game_lines` row (if `pa > 0` or
`ab > 0`), a `pitching_game_lines` row (if `pitch_bf > 0` or `pitch_ip` not
`"0.0"`), and optionally capture the fielding fields alongside the batting
row (schema has room for this even though it's not used in WAR).

`pitch_ip` is already given as a **decimal string like `"0.0"`** (i.e.
already using the baseball convention where `.1`/`.2` mean thirds of an
inning, not real decimals) — do not treat it as a float directly; convert via
the standard `outs = int(whole) * 3 + int(tenths_digit)` transform before
storing as outs-recorded, per the plan's schema design.

`totals` and `pitchers` (win/loss/save attribution) sub-keys of `boxScore`
were seen but not deeply investigated — likely convenience aggregates the
scraper doesn't need since we recompute totals ourselves from the per-player
rows anyway.

`gameData` (sibling of `boxScore` in the payload) has the full linescore
(`runshome1`..`runshome15` etc.), final score, venue, status — maps directly
to the planned `games` table. The **schedule page's `games` array already has
all of this per game for the whole season in one request** — so `games` can
be bulk-ingested from one `schedule-and-results` fetch per competition-year,
and only `status`-final games need a follow-up box-score fetch to get
player-level lines.

## Recommended scraper client design (revises `scraper/api_client.py` vs `dom_scraper.py` split in the plan)

Given no JS execution is needed anywhere, **drop the DOM-scraping /
Playwright-at-runtime path entirely** — a single `scraper/http_client.py`
using `httpx` (with the browser `User-Agent` header, rate limiting, and
caching per the plan) covers everything:
- A small helper to extract the Inertia `data-page` JSON from an HTML
  response (the `HTMLParser`-based extraction used during recon — regex on
  the raw attribute is unreliable if the JSON payload happens to contain a
  literal `"><` sequence; use `html.parser.HTMLParser` instead).
- A small helper (or `lxml`/`selectolax`) to parse the plain HTML tables for
  standings/teams/stats-leaders/player pages.

Playwright stays in the project only as the recon tool it already is
(`scraper/recon/`) — not a runtime dependency for the pipeline. This
simplifies Milestone 3 relative to the original plan.

## Worked example URLs (for direct reuse writing the scraper)

- Schedule + all games for a season: `https://stats.britishbaseball.org.uk/en/events/2026-nbl/schedule-and-results`
- One box score: `https://stats.britishbaseball.org.uk/en/events/2026-nbl/schedule-and-results/box-score/187957`
- Standings: `https://stats.britishbaseball.org.uk/en/events/2026-nbl/standings`
- Stat leaders: `https://stats.britishbaseball.org.uk/en/events/2026-nbl/stats?section=leaders`
- Team list: `https://stats.britishbaseball.org.uk/en/events/2026-nbl/teams`
- One player: `https://stats.britishbaseball.org.uk/en/events/2026-nbl/teams/41852/players/743109`
- Historical editions of a competition: `https://stats.britishbaseball.org.uk/en/events/2026-nbl/editions`
- All 2026 competitions for this federation: `https://stats.britishbaseball.org.uk/en/calendar/2026`
