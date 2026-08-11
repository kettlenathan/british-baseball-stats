# Plan: Defensive metrics — errors by position

Status: **implemented**. Kept as the design record, and as the write-up of what the
box-score payload does and doesn't support defensively.

## Goal

Two questions the app couldn't previously answer:

1. **Per team**: how many errors has each team made *at each position* — i.e. where is a
   team defensively weak?
2. **Per player**: which positions does a player make the most errors at?

Surfaced on the Team Page, the Player Page, and the Scouting Report (both the page and the
PDF, since "their shortstop has 9 errors" is directly actionable when preparing to face
someone).

## Can the existing database answer this? No — but no network re-scrape is needed either

`BattingGameLine` already stores `field_po/field_a/field_e/field_dp` per player per game,
and a `position` column. But that `position` is **the `pos` of the player's last box-score
record only**, while `field_e` is the sum across *all* their records that game. A player who
made an error at 3B and then moved to P has one row reading `position="P", field_e=1`. So
the stored data attributes errors to the wrong position often enough to be unusable, and
carries no per-position breakdown at all.

The information *does* exist in the raw box-score payloads, which are already on disk in
`data/raw_cache/boxscore/` — so this is a **reprocess of cached responses**, not a re-scrape
(see "Backfill" below).

### What the raw payload actually contains

Measured over the cached corpus (4,478 final games; the figures below are from a 500-game
sample unless stated).

**1. Per-record `pos` + `field_e` — authoritative totals, mostly-unambiguous positions.**

Each box-score record carries `pos` and its own fielding counts. `pos` is a *slash-joined
path* of the positions occupied during that stint, not a single position:

```
P (1054)  RF (833)  2B (785)  1B (785)  LF (784)  C (760) ...
3B/P (60)  SS/P (56)  1B/P (37)  P/SS (36)  LF/CF (27)  2B/P/P ...
```

- **81%** of errors sit in a record naming exactly one position → exact attribution.
- **19%** sit in a multi-position record → the record's own errors can't be split by `pos`
  alone.

The per-record totals are trustworthy: summing `field_e` per team per game equals the
payload's own declared `gameData.homeerrors`/`awayerrors` in **989 of 998** team-games
(99.1%). This is what the site itself displays, so it is the number the app must reconcile
to.

**2. Play-by-play `narrative` — exact position numbers, but incomplete.**

`gamePlays.all[inning][half][].narrative` is prose carrying standard scorer's notation:

```
STROMAN Zach reaches on fielding error. E6.
EDMONDS Jordan reaches on throwing error. E4T. COSGROVE Andy scores. 1 RBI.
PALLMANN Philip reaches on dropped fly error. E7. BOSWELL Alexander scores.
```

`E<n>` is the position number (1=P … 9=RF); a `T` suffix marks a throwing error. A token
inventory over the sample found `E1`–`E9` and `E1T`–`E9T` and nothing else error-shaped —
player surnames beginning with E (`EVANS`, `ELLIS`) never match, since the pattern requires a
digit immediately after the `E`.

This feed is **not** complete enough to be the primary source: narrative error counts differ
from the box-score total in **48%** of team-games, because errors on stolen-base throws,
runner advancement, and similar plays aren't always narrated with an `E<n>` token. Using it
alone would undercount and would contradict the site's own box score.

It *is* an excellent **disambiguator**: restricted to the positions a multi-position record
actually names, narrative tokens resolve **93.6%** of the ambiguous 19%.

**3. What does not exist.** `errortype` is a dead field (always 0 across 77,871 plays
sampled), and no play record identifies the *fielder* by id — only `batterid`/`pitcherid`.
So there is no direct position→player mapping at play time; the box-score `pos` path is the
only link between an error and a player.

### Resulting attribution rule

Per game, per team, box-score record by box-score record:

- **Single-position record** → all of its `po/a/e/dp` go to that position. Exact.
- **Multi-position record** → its **errors** are split across the positions it names using
  that game's narrative `E<n>` tokens for that fielding team (the fielding team of a half-
  inning is structural: top = home fielding, bottom = away fielding, same rule
  `_extract_lob` already relies on). Its **PO/A/DP** go to the first position named — the
  position the player started that stint at. This is the one deliberate approximation, and
  it is confined to PO/A/DP on ~5% of records; it never affects errors.
- **Anything still unresolved** → position `"UNK"`, surfaced in the UI rather than hidden.

Net effect: ~99% of errors carry a real position, and summing any player's or team's rows
across positions reproduces the box-score totals exactly.

### Catcher throwing (added after the first cut)

The same box-score records carry `field_sba`, `field_csb` and `field_pb`, which the scraper
was already storing on `BattingGameLine` but nothing had ever aggregated or displayed. Two
things had to be pinned down before building on them, both checked against the *opposing*
team's own batting SB/CS in the same game:

- **`field_sba` is stolen bases allowed, and excludes runners thrown out.** Per team-game it
  equals the opponent's SB in 86% of cases but SB + CS in only 72%. League totals agree:
  79,725 SB batted vs 76,690 `field_sba` fielded. So **attempts = `sba` + `csb`**, and
  dividing by `sba` alone would overstate CS%.
- **`field_csb` is that fielder's caught stealings**, matching the opponent's CS in 96% of
  team-games.

Two consequences worth keeping in the UI copy:

- **Steals allowed are split between the catcher and the pitcher.** In a verified sample the
  catcher carried 11 and the pitcher 2, together exactly matching the opponent's 13 SB. A
  catcher's line is therefore their own share, not every steal the team conceded.
- **The league CS rate is ~2.3%** (1,838 CS against 79,725 SB). That is real, not a parsing
  bug — weak amateur catching, 7-inning games, and runners who run constantly. A catcher's
  rate has to be read against the league figure, never against professional norms.

Attribution differs from PO/A/DP: these are battery stats, so on a multi-position record they
follow **C** (else **P**) wherever it appears in the path, rather than the first position
named. A `3B/C` record's steals happened while catching; crediting them to third base would
invent a third baseman with a caught stealing.

## Schema

Two new tables, following the existing fact/derived split.

**Fact — `fielding_game_lines`** (written only by `scraper/`), unique on
`(game_id, player_season_id, position)`:

| column | meaning |
|---|---|
| `position` | `P C 1B 2B 3B SS LF CF RF DH PH PR UNK` |
| `appearances` | times the player appeared at this position in this game |
| `po` `a` `e` `dp` | attributed per the rule above |
| `sba` `csb` `pb` | steals allowed / caught stealing / passed balls, routed to C (else P) |

**Derived — `fielding_season_stats`** (written only by `stats/`, rebuildable), unique on
`(player_season_id, position)`: the same counting columns summed per season, plus `games`.

Note the fact table is a **superset** of `BattingGameLine`'s fielding columns, not a mirror
of them. `BattingGameLine` is only written when a player actually batted (`pa`/`ab` > 0), so a
defensive substitute who never came to the plate has no batting row — 5,088 player-games
across the corpus, carrying 414 errors and 2,518 steals allowed that were previously stored
nowhere at all. Per player-game the two agree exactly wherever a batting row exists (0
mismatches over 88,370); league-wide they differ by precisely that residue, and each of
`e`/`sba`/`csb`/`pb` was checked to balance to the row.

Team-level per-position totals are summed at read time in `data_access` from the players'
rows, exactly as `team_season_stats` already does for batting/pitching — no third table.

`BattingGameLine`'s existing fielding columns are left untouched: they remain the
per-player-per-game total, and the new table is a strict breakdown of them.

## Backfill

The box-score JSON responses are cached, so this reprocesses local data rather than
re-hitting the site — the same "omit `--last-week` to backfill every already-scraped game"
path CLAUDE.md describes for adding new derived fields:

```
uv run alembic upgrade head
uv run python -m scripts.refresh_data --leagues nbl,d2,d3,d4,d5 --years 2021-2026
```

Caveat: historical seasons are cached ~10 years so 2021-2025 (~3,500 games) is pure local
reprocessing, but **2026 is the current season with a 24h cache TTL**, so its ~976 games
will genuinely re-fetch over the network under the usual rate limiter and circuit breaker.
Budget time for that, and expect it to be the slow part of the run.

## UI

- **Team Page** — "Fielding by position": a position × `G/PO/A/E/DP/FPCT` table and an
  errors-by-position bar chart, plus the players contributing most of the errors at each
  position.
- **Player Page** — "Fielding by position", scoped by the page's existing Career/season
  selector, ordered by errors so "which position do they make the most errors at" is the
  first thing read.
- **Scouting Report** — the opponent's errors by position inline and in the PDF, framed as
  where to attack defensively.
- **Methodology** — documents the attribution rule above, the 19%/93.6%/UNK figures, and the
  PO/A/DP first-position approximation.

## Deliberately out of scope

No defensive WAR component. `stats/war.py` stays offense-only/FIP-only and `WAR_DISCLAIMER`
stays accurate: errors are a *counting* stat with no opportunity denominator here (no innings
by position, no batted-ball location relative to fielder positioning, no chances-not-reached),
so nothing in this data supports a runs-above-average defensive metric. Fielding percentage
is shown for context but is a weak metric for the same reason — a fielder with poor range
records fewer errors, not more.
