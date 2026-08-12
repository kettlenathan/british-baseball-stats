import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from stats import constants
from stats.shrinkage import (
    FALLBACK_BATTING_STABILIZATION_PA,
    FALLBACK_PITCHING_STABILIZATION_IP,
    MIN_QUALIFYING_PLAYERS,
)
from stats.war import WAR_DISCLAIMER

st.set_page_config(page_title="Methodology", page_icon="📖", layout="wide")
st.title("Methodology")
st.caption(
    "How the stats on this site are collected and calculated, and where they "
    "diverge from official Baseball-Reference/FanGraphs definitions."
)

st.subheader("Data source")
st.markdown(
    "All data is scraped from [stats.britishbaseball.org.uk](https://stats.britishbaseball.org.uk), "
    "the official stats platform for the British Baseball Federation. Box scores are only "
    "pulled in once a game is marked `final` on the source site, so figures shown here "
    "should match the federation's own published totals."
)

st.divider()

st.subheader("Divisions, and why two versions of wRC+ are shown")
st.markdown(
    "Most of these leagues are split into **regional divisions** — 2026's Division 3 runs "
    "North, Central, South and SWWBL — and during the regular season a team plays *only* "
    "the other teams in its own division. Across all five leagues in 2026 there were 1,231 "
    "regular-season games and **not one** of them was between divisions."
)
st.markdown(
    "That has a consequence worth being blunt about: **two teams in different divisions have "
    "no games in common**, so their records cannot be directly compared. In 2026 the Milton "
    "Keynes Bucks went 18-0 in Division 3 Central while the London Meteors went 19-5 in "
    "Division 3 South, and the two never met. Nothing on this site currently tells you which "
    "of those is the better record, because the games needed to answer that were never "
    "played. League tables are therefore shown one division at a time rather than as a single "
    "ranked list."
)
st.markdown(
    "Divisions also differ a lot in how much scoring happens in them. In 2026's Division 4, "
    "the North division averaged **11.34 runs per team per game** and the London division "
    "**7.52** — a league-average wOBA of .513 against .415, inside the same competition. "
    "So the leaderboards show two versions of the same comparison:"
)
st.markdown(
    "- **wRC+** (and **ERA+**) measures a player against the whole competition — every team "
    "in that league and season, pooled.\n"
    "- **wRC+ vs Div** (and **ERA+ vs Div**) measures them against their own division only — "
    "the opposition they actually faced."
)
st.markdown(
    "**Neither one is the truer number.** The league-wide version compares a player against "
    "opponents they never played. The division version compares them against the opponents "
    "they did play, but against a bar that may itself be unusually high or low. A large gap "
    "between the two means that player's division had an unusual run environment — not that "
    "the player is over- or under-rated."
)
st.markdown(
    "In particular, the division figure **cannot** tell you which division is stronger. A "
    "low-scoring division might have better pitching or weaker hitting, and its own games "
    "alone can never separate those two explanations. Working out relative division strength "
    "needs evidence that links divisions together — players who appear in more than one, or "
    "the handful of cross-division games in earlier seasons — and that is not yet built."
)
st.markdown(
    "Divisions are read from the source site's own standings page, which covers every season "
    "back to 2021. They are treated as belonging to a single season rather than persisting "
    "across years, because the source gives no stable identity for them: the same regional "
    "grouping is published as `AA - Central` in 2021, `South A` in 2022, `South` in 2023, `A` "
    "in 2024 and `North` in 2026, and the same name is reused for different regions in "
    "different years. A small number of teams appear in the fixture list but in no published "
    "standings table; they're shown under **Not in a published division** rather than being "
    "assigned to one by guesswork."
)
st.markdown(
    "Playoff games are separated from regular-season games and excluded from league tables "
    "and from each division's run-environment figures. A playoff between two division winners "
    "belongs to neither division, and counting the ones that happen to be intra-division "
    "would make a division's numbers depend on how the bracket fell."
)

st.divider()

st.subheader("Team strength rating and strength of schedule")
st.markdown(
    "Even inside a single division, schedules aren't balanced. In 2026's Division 3 Central, "
    "Milton Keynes played bottom-placed Essex Archers four times and second-placed Cambridge "
    "Sovereigns only twice — while Cambridge played Essex **six** times and Milton Keynes "
    "twice. Two records built on schedules like that aren't quite measuring the same thing."
)
st.markdown(
    "The **Rating** column estimates each team's strength from *who they actually played*, "
    "using a Bradley-Terry model — the standard approach for ranking from head-to-head "
    "results. It's on a log-odds scale where **0 is a division-average team**; +1 means "
    "beating that average team about 73% of the time at a neutral venue. **SOS** is how much "
    "harder the schedule was than an even draw within the same division would have been, so "
    "a negative SOS means an easier run than the raw record suggests."
)
st.markdown(
    "Two deliberate choices. The rating uses **only who won**, not the score — run margins in "
    "this league are dominated by blowouts (a third of games are decided by 10 or more) and "
    "are cut short by mercy rules, so a big margin often records when a game was stopped "
    "rather than how one-sided it was. And the estimate is **pulled toward the division "
    "average**, more so for teams with few games, which is also what stops an undefeated "
    "record producing an infinite rating."
)
st.markdown(
    "SOS is measured against a balanced draw rather than as a simple average of opponents "
    "faced, because a team never plays itself: under a simple average the best team in any "
    "division would automatically appear to have had the easiest schedule, which is an "
    "artefact rather than a finding."
)
st.markdown(
    "**These ratings compare teams within one division only.** Divisions play no "
    "regular-season games against each other, so each division's ratings are centred on its "
    "own average, and a +1.5 in one division does not mean the same thing as a +1.5 in "
    "another. Comparing them across divisions would assume the divisions are equally strong, "
    "which is the question, not the answer."
)
st.markdown(
    "Tested by holding out games and predicting them: the rating beats ranking teams by win "
    "percentage by a small margin overall (0.7% on log loss), and that margin is "
    "concentrated in the teams with lopsided schedules, where it is around three and a half "
    "times larger. Where a schedule is already balanced, the rating and the raw record agree "
    "— which is the point. Both are far better than guessing or than always picking the home "
    "team."
)

st.divider()

st.subheader("Comparing teams in different divisions")
st.markdown(
    "Because divisions never meet in the regular season, nothing in the results themselves "
    "says whether one division is stronger than another. The Division Strength page "
    "estimates it a different way: from **players who turned out in more than one "
    "division**. When the same person bats in two, the difference in what they produce is "
    "evidence about the difference between those divisions — the same reasoning behind "
    "Major League Equivalencies."
)
st.markdown(
    "Across the seasons here, 1,221 pairs of divisions share at least one player, which is "
    "enough to link every division into a single connected network. The estimate uses only "
    "*within-player* differences, so a player who appeared in one division contributes "
    "nothing at all and cannot make their division look easy simply by being a good hitter."
)
st.markdown(
    "The result is an **offset** in wOBA points saying how much easier a division was to "
    "bat in, which implies weaker pitching. Turning that into a win probability needs one "
    "further number — how much a wOBA point of difficulty is worth in games — and that is "
    "fitted against the archive's cross-division games, mostly playoffs."
)
st.markdown(
    "**How much to trust it.** Held out against 305 cross-division games that none of the "
    "inputs had seen, this predicts better than assuming the divisions are equal, by about "
    "4% on log loss, and the fitted conversion lands on the theoretically expected sign "
    "every time. That is a real result. But the margin is only around twice its own "
    "standard error, and the players who move between divisions are not a random sample — "
    "someone guesting down a level is likely stronger than the division they are visiting, "
    "which inflates the apparent gap in a way nothing here corrects for."
)
st.markdown(
    "**What it is used for, and what it deliberately is not.** The same-player comparison "
    "agrees closely with the simple scoring-level comparison — the two correlate at about "
    "0.82 within a league-season — so it is not shown as a better answer to the same "
    "question. It is shown because it answers a *different* one: a division scores heavily "
    "either because its pitching was weak or because its hitters were strong, and only the "
    "same-player reading can separate those. The Division Strength page therefore reports "
    "both readings and the difference between them, rather than a single adjusted number."
)
st.markdown(
    "It stops there deliberately. This site does **not** publish a probability that a team "
    "from one division would beat a team from another. With roughly 22 games per season, "
    "the uncertainty in a club's own strength is far larger than the uncertainty about its "
    "division — it accounts for about 96% of the variance in such a comparison — and around "
    "three quarters of cross-division pairings cannot be separated at all. A percentage "
    "would imply a precision the data does not have, however carefully it were captioned."
)

st.divider()

st.subheader("Forfeits, unscored games, and seasons still in progress")
st.markdown(
    "Not every result on the record came from a game that was played. Two kinds turn up "
    "throughout this data:"
)
st.markdown(
    "- **Forfeits** — awarded 7-0 without anyone taking the field.\n"
    "- **Result-only games** — genuinely contested, with a real score like 14-8 recorded, "
    "but no scoresheet ever entered, so there are no innings, no line score and no player "
    "statistics."
)
st.markdown(
    "Both count toward **records and standings**, matching the federation's own published "
    "tables — 588 such games across the seasons here. Neither counts toward **runs, rate "
    "stats or a division's scoring environment**, because no runs were actually scored in a "
    "forfeit and no at-bats were recorded in either. A team's runs per game is therefore per "
    "game *played*, which can be fewer than the games in its win-loss record."
)
st.markdown(
    "Separately: the source site publishes each season's **full fixture list in advance**, so "
    "where a season is still running the app can say how much of it has happened rather than "
    "presenting a partial table as if it were final. Standings and ratings for a live season "
    "are a snapshot of games played to date, and team pages show how many fixtures remain "
    "and whether the run-in is harder or easier than the season so far. Scheduled games never "
    "affect a rating — only results do."
)

st.divider()

st.subheader("Following a player across seasons")
st.markdown(
    "The source site issues a **new player ID every season** — the same player on the same "
    "team gets a different ID in 2024, 2025 and 2026 — so a career can't be assembled from "
    "those IDs alone. Players are instead matched across seasons on their **name plus birth "
    "year**, with the name compared after folding away capitalisation, accents and "
    "punctuation (the source spells the same player `Adam MURRAY`, `ADAM MURRAY` and "
    "`Adam Murray` in different seasons)."
)
st.markdown(
    "Matching is deliberately cautious: **both** the name and the birth year must agree. "
    "Plenty of names in this league belong to more than one real person — there are four "
    "different Ben Carters — so matching on name alone would silently merge two players' "
    "careers into one. Where the source has no birth year, or an obviously bogus one (a "
    "birth year of `1` or `2021` appears on around 110 records), those seasons are left as "
    "separate players rather than guessed at."
)
st.markdown(
    "The source also sometimes records a birth year that is simply **wrong**, which would "
    "split one player in two. Those are caught using squad numbers: a team can't field two "
    "players wearing the same number in the same season, so the same name in the same "
    "number for the same club, in seasons that never overlap, is one player — whatever the "
    "birth years say. Where two players genuinely do overlap, they're left separate."
)
st.markdown(
    "Finally, where two different players really do share a name, they're shown with their "
    "birth year attached (`Ben CARTER (b. 1995)`) so their records stay separate rather "
    "than being added together."
)

st.divider()

st.subheader("wOBA and wRC+")
st.markdown(
    "**wOBA** (weighted On-Base Average) values every way of reaching base by how many runs "
    "it's actually worth, instead of treating a walk and a home run as equal like OBP does:"
)
st.latex(
    r"""
    wOBA = \frac{%.2f \cdot uBB + %.2f \cdot HBP + %.2f \cdot 1B + %.2f \cdot 2B + %.2f \cdot 3B + %.2f \cdot HR}
    {AB + BB - IBB + SF + HBP}
    """
    % (
        constants.WOBA_WEIGHT_UBB,
        constants.WOBA_WEIGHT_HBP,
        constants.WOBA_WEIGHT_1B,
        constants.WOBA_WEIGHT_2B,
        constants.WOBA_WEIGHT_3B,
        constants.WOBA_WEIGHT_HR,
    )
)
st.markdown(
    "**wRC+** expresses a player's wOBA relative to the league average for that league-season "
    "(100 = league average, 120 = 20% better than average, and so on): "
    "`100 × (player wOBA / league wOBA)`."
)

st.subheader("FIP and ERA+")
st.markdown(
    "**FIP** (Fielding Independent Pitching) scores a pitcher only on the outcomes they control "
    "directly — strikeouts, walks, hit-by-pitches, and home runs — since no fielder positioning "
    "or range data exists for this league to judge defense separately from pitching:"
)
st.latex(
    r"""
    FIP = \frac{%.1f \cdot HR + %.1f \cdot (BB + HBP) - %.1f \cdot SO}{IP} + FIP_{constant}
    """
    % (constants.FIP_WEIGHT_HR, constants.FIP_WEIGHT_BB_HBP, constants.FIP_WEIGHT_SO)
)
st.markdown(
    "**ERA+** expresses a pitcher's ERA relative to the league average, inverted so higher is "
    "still better: `100 × (league ERA / player ERA)`."
)

st.divider()

st.subheader("What's self-calibrated to this league vs. fixed")
st.markdown(
    "The **linear weight coefficients** above (the numbers in front of each stat in the "
    "formulas) are fixed, published sabermetric constants — deriving them from scratch needs "
    "a run-expectancy matrix built from play-by-play data, which this league doesn't have. "
    "Published research (Tom Tango et al., *The Book*) shows these coefficients are fairly "
    "stable across different run environments, so using fixed values here is a reasonable "
    "approximation."
)
st.markdown(
    "What **is** calculated fresh from this league's own scraped data, separately for every "
    "league and season:"
)
st.markdown(
    "- League-average wOBA, OBP, SLG, ERA, and FIP\n"
    "- The FIP additive constant (solved so league FIP equals league ERA that season)\n"
    "- The runs-per-win conversion rate, scaled from the traditional \"10 runs = 1 win\" "
    f"reference (at {constants.REFERENCE_RUNS_PER_GAME} runs/game/team) by this league's own "
    "actual scoring rate"
)
st.markdown(
    "This is what makes a \"0 WAR\" or \"100 wRC+\" player here mean *league-average within "
    "this league's own actual run environment* — not relative to MLB or any other league."
)

st.divider()

st.subheader("WAR — what it is and isn't")
st.warning(WAR_DISCLAIMER)
st.markdown(
    "In more detail, this WAR calculation is missing three things a full implementation "
    "would have:\n"
    "- **Park factors** — no per-venue run environment data exists for this league, so every "
    "park is treated as neutral.\n"
    "- **A defensive component** — there's a coarse batted-ball proxy (pull direction, distance, "
    "ground/fly/line/pop type — see the section below), but no true field coordinates, exit "
    "velocity, or fielder positioning/range data, so batting WAR is offense-only and pitching WAR "
    "is FIP-only; a plus defender and a poor one with identical batting/pitching lines get the "
    "same WAR here.\n"
    "- **League-specific linear weights** — the coefficients are fixed published constants "
    "(see above) rather than solved from this league's own play-by-play, since that needs a far "
    "larger sample than this league's scale can support for a stable result.\n\n"
    f"Formula version: `{constants.FORMULA_VERSION}` (stored alongside every computed WAR row, "
    "so historical values stay traceable to the formula that produced them if it's ever revised)."
)

st.divider()

st.subheader("True talent (empirical-Bayes shrinkage)")
st.markdown(
    "A batter with 15 PA and a .500 wOBA almost certainly isn't actually a .500 hitter — with "
    "this few PA, most of that number is sampling noise. **True talent wOBA/FIP** regresses each "
    "player's observed rate toward the league-season mean, weighted by how much playing time "
    "they've actually had:"
)
st.latex(r"\text{shrunk} = \frac{n \cdot \text{observed} + k \cdot \text{league mean}}{n + k}")
st.markdown(
    "where `n` is PA (batters) or IP (pitchers), and `k` is a **stabilization point** — the "
    "sample size at which observed and league-average performance are weighted equally. Rather "
    "than borrowing a fixed published stabilization point, `k` is estimated from this "
    "league-season's own player population: from the league-wide event rates, treating each "
    "wOBA/FIP linear-weight event as an independent random process (a standard simplification "
    "in stabilization-point research) gives an estimate of *within-player* sampling noise; "
    "comparing that to the *actual* spread of observed rates across players (once enough players "
    "clear a minimum sample) isolates the *between-player* \"true talent\" variance, and `k` "
    "falls out as the ratio of the two."
)
st.markdown(
    f"If a league-season's own data can't support that estimate — fewer than "
    f"{MIN_QUALIFYING_PLAYERS} qualifying players, or the variance decomposition doesn't come "
    "out positive, both plausible in a small amateur league-season — this falls back to a "
    f"published stabilization point instead ({FALLBACK_BATTING_STABILIZATION_PA:.0f} PA for "
    f"batters, {FALLBACK_PITCHING_STABILIZATION_IP:.0f} IP for pitchers, from FanGraphs/Russell "
    "Carleton's stabilization research). Which path was used is shown alongside the shrunk value "
    "wherever it's displayed."
)
st.markdown(
    "This is applied to every player-season regardless of sample size — a player with zero PA "
    "simply reduces to the league mean with 0% reliability, which is the point: the smallest "
    "samples are exactly who benefits most from this adjustment."
)

st.divider()

st.subheader("Batted-ball tendency, spray charts, matchups, and first-pitch-strike%")
st.markdown(
    "This league's scorers don't record true batted-ball field coordinates or exit velocity "
    "(those fields are always empty in the source data) — but they do record a directional "
    "**pull value** (roughly which side of the field a ball was hit to) and a **hit distance** "
    "for every ball put in play. That's enough for an approximation of a spray chart and a "
    "pull-tendency read, just not a to-scale field diagram."
)
st.markdown(
    "**Pull / Center / Oppo tendency**: every batted ball's pull value is adjusted for the "
    "batter's own handedness (so \"pulled\" always means the same thing regardless of which "
    "side someone bats from), then bucketed against **fixed thirds of the true 90-degree "
    "fair-territory fan** — the middle 30 degrees (+/-15 degrees off dead-center) is Center, "
    "the outer 15-45 degrees on the batter's pull side is Pull, and the same range on the "
    "other side is Oppo. Unlike the league-average wOBA/FIP above, this is deliberately **not** "
    "self-calibrated to this league's own batted-ball distribution — a real ballpark's foul "
    "lines don't move with it, so neither does \"pulled\". A player's tendency label is "
    "whichever third holds the most of their own batted balls. **Switch hitters are excluded** "
    "from this entirely — there's no per-plate-appearance record of which side they actually "
    "batted from in a given at-bat, so classifying them would risk mislabeling roughly half "
    "their pulled balls as opposite-field and vice versa."
)
st.markdown(
    "**Spray chart**: plotted on a radial (polar) chart — angle from the raw pull direction, "
    "distance from home plate as the radius — approximating a real spray chart's shape without "
    "claiming to be a precise field-location plot. The pull value is treated as degrees off "
    "dead-center field and clamped to the true +/-45 degrees of a real ballpark's fair "
    "territory (foul line to foul line), so no point ever plots outside the field."
)
st.markdown(
    "**Direction heatmap**: a second chart alongside the spray chart, on the same schematic "
    "field and the same +/-45 degree fan (with the surrounding polar grid/boundary dropped — "
    "only the field lines themselves frame the chart), but dropping hit distance entirely — a "
    "handful of raw distance values are negative, which is impossible, so distance is the less "
    "trustworthy of the two fields. Batted balls are bucketed into angular wedges spanning the "
    "full field depth, colored red for the most common directions and blue for the least "
    "common, to give a distance-independent read on where a player's contact actually goes."
)
st.markdown(
    "**First-pitch-strike%**: the source data's own pitch-result flags (ball/called "
    "strike/swinging strike/foul/in play) are never populated for this league, so a first-pitch "
    "strike is instead inferred by comparing the ball-strike count on the first pitch of a plate "
    "appearance to the next pitch in that same plate appearance — if the strike count went up (or "
    "the first pitch itself ended the at-bat as a ball in play, not a walk or hit-by-pitch), it "
    "counts as a first-pitch strike."
)
st.markdown(
    "**Batter-vs-pitcher matchups**: aggregated directly from plate-appearance results, shown "
    "for both season and career scope. There's **no minimum plate-appearance threshold** — a "
    "single at-bat between two players shows up the same as a 20-at-bat history, so treat small "
    "samples with appropriate skepticism."
)

st.divider()

st.subheader("Errors by position")
st.markdown(
    "The Team Page, Player Page and Scouting Report break errors down by fielding position. "
    "Getting there takes two sources, because neither one is sufficient alone."
)
st.markdown(
    "**Where the totals come from**: the box score records each player's putouts, assists, "
    "errors and double plays per game, alongside a position field. Those totals are "
    "authoritative — summed per team they match the site's own published team error count in "
    "**99%** of team-games — so they're what every number here reconciles to."
)
st.markdown(
    "**Where the position comes from**: the box score's position field is a *path*, not a single "
    "position — a player who moved from shortstop to the mound mid-game is recorded as `SS/P`, "
    "with one combined error total. About **81%** of errors sit in a record naming exactly one "
    "position and are attributed directly. For the other 19%, the play-by-play commentary is "
    "parsed for standard scorer's notation (`E6` for a fielding error by the shortstop, `E4T` "
    "for a throwing error by the second baseman), which resolves **94%** of them. Putouts, "
    "assists and double plays on those split records are credited to the first position named — "
    "the position the player started that stint at — which is the one approximation involved, "
    "and it never affects error counts."
)
st.markdown(
    "**UNK**: roughly **1.5%** of errors are placed by neither source. They're shown as `UNK` "
    "rather than dropped or guessed at, so the per-position rows always add up to the team's "
    "and player's real error totals."
)
st.markdown(
    "**Why the play-by-play isn't used for the counts themselves**: its error notation misses "
    "errors on stolen-base throws and runner advancement, and disagrees with the box score's own "
    "totals in about half of team-games. It's reliable for *which position* an error belongs to "
    "and unreliable for *how many* there were, so it's used only for the former."
)
st.markdown(
    "**Catcher throwing**: the box score records stolen bases allowed and runners caught "
    "against each fielder. Stolen bases allowed **exclude** runners thrown out, so the attempts "
    "column is *allowed + caught*, and CS% is caught ÷ attempts. This was checked against the "
    "opposing team's own SB/CS totals for the same game, which the fielding numbers reproduce in "
    "96–99% of team-games."
)
st.markdown(
    "Two caveats worth knowing before reading a catcher's CS%. This league's scorers charge part "
    "of a team's steals allowed to the **pitcher** rather than the catcher, so a catcher's line "
    "is their own share and not every steal the team conceded. And CS% is as much a property of "
    "the pitching staff — how quickly they get to the plate, whether they hold runners — as of "
    "the catcher's arm. League-wide, catchers here throw out only a low single-digit percentage "
    "of runners, so compare against the league figure shown alongside rather than against "
    "professional norms."
)
st.warning(
    "**Errors are not a fielding-quality metric.** They have no opportunity denominator here — "
    "there are no innings-by-position, no batted-ball locations relative to where a fielder was "
    "standing, and no record of balls a fielder never reached. A player with poor range records "
    "*fewer* errors, not more, and shortstops and third basemen out-error corner outfielders on "
    "every team, so the only meaningful comparison is against the same position elsewhere in the "
    "league (the \"E vs League\" column). Fielding % inherits the same weakness. This is why "
    "**WAR here has no defensive component at all** — see above."
)

st.divider()

st.subheader("Batter archetypes")
st.markdown(
    "The Batter Archetypes page groups batters within one league-season using unsupervised "
    "clustering (k-means), based on **six** features: Net Pull% (Pull% minus Oppo%), BB%, K%, "
    "and 2B%/3B%/HR% (each hit type as a share of total hits). Every feature is standardized "
    "(z-scored) before clustering, since a raw-scale feature would otherwise dominate the "
    "percentage-based ones."
)
st.markdown(
    "**Why not more features**: earlier versions also included ISO, Center%, 1B%, and raw "
    "Pull%/Oppo% as two separate features, but all were dropped as redundant. ISO is a weighted "
    "recombination of the same doubles/triples/home-run events that 2B%/3B%/HR% already "
    "describe at finer granularity — including both would let one underlying signal (power) "
    "count twice toward clustering distance, silently outweighing plate discipline or spray "
    "direction. Center% and 1B% are each the \"remainder\" share of a group that sums to 100% "
    "(Pull/Center/Oppo; 1B/2B/3B/HR) — a fixed function of the other shares in its group, adding "
    "collinearity without adding information; dropping one category per compositional group is "
    "the standard treatment for this. Pull% and Oppo% go a step further: even with Center% "
    "already dropped, the two remaining shares still only carry **one** real axis of variation "
    "between them (how pulled a batter's contact is) — so they're combined into a single signed "
    "Net Pull% feature instead of being kept as two separately-weighted, near-mirror-image ones. "
    "ISO, Center%, and raw Pull%/Oppo% are still shown in the page's tables for context — they "
    "just aren't clustering inputs."
)
st.markdown(
    "**Choosing k**: rather than a fixed number of archetypes, k-means is fit across a range of "
    "candidate k values and the one that maximizes mean silhouette score (a standard measure of "
    "how well-separated the resulting clusters are) is picked automatically — visible in the "
    "\"How k was chosen\" section of that page. **Archetype labels** (e.g. \"High HR%, High Net "
    "Pull%\") are generated automatically from each cluster's two most extreme standardized "
    "features, rather than drawn from a fixed, presumptuous taxonomy of hitter types."
)
st.markdown(
    "**Reading the scatter plot**: clustering runs on the full 6-feature standardized space, not "
    "the 2D plot itself — the plotted x/y position is a separate PCA projection computed purely "
    "for visualization. Each axis is labeled with whichever features load most heavily onto it "
    "(the same \"top features\" technique used for archetype labels above) rather than a bare "
    "\"Component 1\"/\"Component 2\", so the plot reads as e.g. \"more Net Pull%\" left-to-right "
    "instead of an unlabeled abstract axis — but position along an axis is still relative, not a "
    "stat value in its own right."
)
st.markdown(
    "**Batted-ball type (ground ball/fly ball/line drive/pop up) is deliberately not included** "
    "in the feature set. This league's batted-ball hit-distance field is already known to be "
    "unreliable (see the note above about negative distance values) — and any attempt to "
    "classify batted-ball type from the source data's raw `hittype` code would lean on that same "
    "distance field, since there's no other documentation of what the code actually means. The "
    "extra-base-hit mix used instead is derived purely from already-reliable counting stats "
    "(hits, doubles, triples, home runs), with no such dependency."
)
st.markdown(
    "As with the matchup tables above, there's no minimum-sample-size filter beyond the page's "
    "own PA slider — batters near that threshold still reflect noisy underlying rates, so "
    "raising the minimum PA gives a cleaner read at the cost of a smaller population to cluster."
)

st.subheader("Scouting reports: probable pitchers and the lineup optimizer")
st.markdown(
    "The Scouting Report page infers **probable pitchers** from usage rather than any published "
    "rotation (the league doesn't publish one). The starter of each played game is identified "
    "from the play-by-play — whoever threw the team's first defensive plate appearance — falling "
    "back to \"most outs recorded in that game\" for games with no play-by-play feed. Each start "
    "is then weighted by an exponential recency decay (half-life two weeks, measured from the "
    "team's own most recent game, not today's date, so historical seasons rank sensibly), and "
    "pitchers are ranked by their summed weight. The confidence label reflects each pitcher's "
    "share of that recency-weighted total. This is an informed guess about tendencies, not a "
    "prediction — weekend doubleheaders usually mean two different starters."
)
st.markdown(
    "The **lineup optimizer** turns each available batter into per-PA probabilities of "
    "walk/HBP/single/double/triple/HR/out. The overall quality of the profile is anchored to the "
    "batter's *true-talent* (shrunk) wOBA from the shrinkage layer above — the raw season line "
    "only contributes the *shape* (their observed mix of hit types), scaled to match that target. "
    "Walk and HBP rates are shrunk toward league average with the published batting "
    "stabilization point. When the opposing starter's throwing hand is known, the target wOBA is "
    "platoon-adjusted: the batter's observed career vs-hand wOBA is shrunk toward their overall "
    "talent with a much larger stabilization point (300 PA), because platoon splits stabilize "
    "very slowly and a dozen PA against lefties is nearly all noise."
)
st.markdown(
    "Rankings that pick between players (who starts, who's the first bat off the bench) use a "
    "**sample-penalized score**, not the shrunk estimate alone: the estimate minus an uncertainty "
    "penalty that shrinks with the player's own plate appearances. This matters because shrinkage "
    "parks a near-empty sample at league average — without the penalty, an 0-for-7 hitter "
    "(estimate ≈ league average) would out-rank a proven slightly-below-average bat with 60 PA. "
    "On top of that, no one is *named* a best/first-choice option — pinch-hit roles, per-slot "
    "stat claims — on fewer than 20 PA this season; below that they're explicitly marked as "
    "having too few PA to judge."
)
st.markdown(
    "When more than nine hitters are available, the **nine best adjusted bats start** and the "
    "rest are listed as the bench, each rated as a pinch-hit option against left- and "
    "right-handed pitching (the same platoon-adjusted estimate, computed for both hands). The "
    "recommendation itself is displayed in ordinary box-score terms — each slot's line cites the "
    "hitter's OBP, extra-base power (ISO), strikeout rate, steals, or walk rate *ranked within "
    "that specific nine* — because shrunk wOBA values cluster tightly together and don't explain "
    "anything a coach can act on. The internal model values remain visible in the page's "
    "\"under the hood\" expander."
)
st.markdown(
    "Expected runs for a batting order come from an exact Markov chain over the 24 base-out "
    "states, cycling through the order for 7 innings (the BBF game length), with fixed runner "
    "advancement: walks force runners; a single scores runners from 2nd and 3rd and moves the "
    "runner on 1st up one base; a double scores runners from 2nd and 3rd and sends the runner on "
    "1st to 3rd; triples and homers clear the bases; outs advance nobody. There are no steals, "
    "sacrifices, double plays, or errors in the model, and mercy/curfew rules are ignored — they "
    "truncate blowouts roughly equally whichever order you bat in, so they change absolute run "
    "totals, not which order is best. The search seeds a conventional order (best hitters in the "
    "first four slots, on-base ahead of power) and hill-climbs over pairwise swaps with fixed "
    "random restarts, so the same inputs always produce the same recommendation. Differences "
    "between sensible orders are fractions of a run per game — the recommendation is a "
    "tiebreaker, not a verdict."
)
st.markdown(
    "The scouting PDF re-renders spray charts with the same fixed 90° fan geometry as the app's "
    "own charts, and every table in it uses the same true-talent rankings and no-minimum-sample "
    "matchup caveats described above."
)

st.divider()
st.caption(
    "Spotted something that looks wrong, or have a question about how a stat is calculated? "
    "Use the Feedback & Support page in the sidebar."
)
