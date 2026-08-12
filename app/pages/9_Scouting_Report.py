import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import streamlit as st

from app.components.charts import spray_chart, spray_heatmap
from app.components.data_access import (
    all_team_names,
    batter_spray_points,
    batter_tendency,
    data_freshness,
    league_catcher_throwing,
    league_fielding_by_position,
    lineup_recommendation,
    list_league_seasons,
    next_fixtures,
    pitcher_spray_points,
    pitcher_vs_hands,
    roster_vs_pitcher,
    scouting_hitters,
    scouting_pitching_staff,
    standings,
    team_catcher_throwing,
    team_fielding_by_position,
    team_position_error_players,
    team_recent_games,
    team_roster,
    team_season_stats,
)
from app.components.filters import league_season_selector
from app.components.formatting import column_config_for
from app.components.scouting_pdf import build_scouting_pdf

st.set_page_config(page_title="Scouting Report", page_icon="🎯", layout="wide")
st.title("🎯 Scouting Report")
st.caption(
    "Prepare for an opponent: their best hitters and spray tendencies, the pitchers you're likely "
    "to face, and a recommended batting order for your own side — downloadable as a PDF."
)

# How many opposing hitters get full spray-chart detail blocks in the PDF
# (everyone still appears in the summary table), and the floor below which a
# hitter's sample is too thin to bother charting.
PDF_TOP_HITTERS = 6
MIN_PA_FOR_DETAIL = 20
PDF_TOP_PITCHERS = 3

league_season_id = league_season_selector()
if league_season_id is None:
    st.stop()

season_teams = sorted(
    team_roster(league_season_id)["team"].unique().tolist()
    or [t for t in all_team_names()]
)
if not season_teams:
    st.info("No teams found for this league/season.")
    st.stop()

col_us, col_them = st.columns(2)
with col_us:
    our_team = st.selectbox("Your team", season_teams, key="scout_our_team")

fixtures = next_fixtures(league_season_id, our_team)
default_opponent_idx = 0
opponents = [t for t in season_teams if t != our_team]
if not fixtures.empty and fixtures.iloc[0]["opponent"] in opponents:
    default_opponent_idx = opponents.index(fixtures.iloc[0]["opponent"])
with col_them:
    opponent = st.selectbox("Opponent", opponents, index=default_opponent_idx, key="scout_opponent")

next_meeting = None
if not fixtures.empty:
    against = fixtures[fixtures["opponent"] == opponent]
    if not against.empty:
        next_meeting = against.iloc[0].to_dict()
        venue = f" at {next_meeting['venue']}" if next_meeting.get("venue") else ""
        st.caption(
            f"Next meeting: **{next_meeting['game_date']}** ({next_meeting['home_away']}){venue}"
        )

st.divider()

# --------------------------------------------------------------------------
# Opponent preview (inline, lighter than the PDF)
# --------------------------------------------------------------------------

hitters = scouting_hitters(league_season_id, opponent)
staff = scouting_pitching_staff(league_season_id, opponent)

st.subheader(f"{opponent}: probable pitchers")
st.caption(
    "Inferred from usage — who has started, weighted toward recent weekends. The league publishes "
    "no rotations, and weekend doubleheaders usually mean two different starters."
)
probables = staff[staff["gs"] > 0].head(PDF_TOP_PITCHERS) if not staff.empty else staff
if probables is None or probables.empty:
    st.info("No pitching usage data for this opponent yet.")
else:
    display_cols = [
        "player", "throws", "g", "gs", "ip", "team_ip_share", "era", "whip", "k9", "bb9",
        "fip", "fps_pct", "confidence", "evidence",
    ]
    shown = probables[[c for c in display_cols if c in probables.columns]]
    st.dataframe(shown, hide_index=True, use_container_width=True, column_config=column_config_for(shown))

expected_hand = None
if probables is not None and not probables.empty:
    top_hand = probables.iloc[0]["throws"]
    expected_hand = top_hand if top_hand in ("L", "R") else None
hand_options = {"Use top probable starter": expected_hand, "Left-handed": "L", "Right-handed": "R", "Unknown": None}
hand_label = st.radio(
    "Optimize our lineup against",
    list(hand_options),
    horizontal=True,
    help="Override if you already know who's starting against you.",
)
vs_throws = hand_options[hand_label]

with st.expander(f"{opponent}: hitters", expanded=False):
    if hitters.empty:
        st.info("No batting data for this opponent yet.")
    else:
        cols = [
            "player", "bats", "pa", "avg", "obp", "slg", "woba", "shrunk_woba", "wrc_plus",
            "hr", "sb", "bb_pct", "k_pct", "tendency",
        ]
        shown = hitters[[c for c in cols if c in hitters.columns]]
        st.dataframe(shown, hide_index=True, use_container_width=True, column_config=column_config_for(shown))
        pick = st.selectbox("Spray chart", hitters["player"], key="scout_hitter_spray")
        season_points = batter_spray_points(pick, league_season_id)
        career_points = batter_spray_points(pick)
        c1, c2 = st.columns(2)
        with c1:
            st.caption("This season")
            st.plotly_chart(spray_chart(season_points), use_container_width=True, key="scout_spray_season")
        with c2:
            st.caption("Career")
            st.plotly_chart(spray_chart(career_points), use_container_width=True, key="scout_spray_career")
        if len(season_points) >= 25:
            st.caption("This season, direction density")
            st.plotly_chart(spray_heatmap(season_points), use_container_width=True, key="scout_spray_heat")

# --------------------------------------------------------------------------
# Opponent defence
# --------------------------------------------------------------------------

st.subheader(f"{opponent}: defence by position")
st.caption(
    "Where they give away outs. Compared against the average team's errors at the *same* position, "
    "since shortstops and third basemen out-error corner outfielders on every team."
)

opponent_defense = team_fielding_by_position(league_season_id, opponent)
if not opponent_defense.empty:
    league_defense = league_fielding_by_position(league_season_id)
    if not league_defense.empty and "e_per_team" in league_defense.columns:
        opponent_defense = opponent_defense.merge(
            league_defense[["position", "e_per_team"]], on="position", how="left"
        )
        opponent_defense["e_vs_league"] = opponent_defense["e"] - opponent_defense["e_per_team"]

defense_error_players = team_position_error_players(league_season_id, opponent)

if opponent_defense.empty:
    st.info("No fielding data for this opponent yet.")
else:
    st.dataframe(
        opponent_defense,
        hide_index=True,
        use_container_width=True,
        column_config=column_config_for(opponent_defense),
    )
    if "e_vs_league" in opponent_defense.columns:
        weak = opponent_defense[opponent_defense["e_vs_league"] > 0].sort_values(
            "e_vs_league", ascending=False
        )
        if not weak.empty:
            spots = ", ".join(
                f"**{r.position}** ({int(r.e)} E, {r.e_vs_league:+.1f} vs league)"
                for r in weak.head(3).itertuples()
            )
            st.markdown(f"Worth testing: {spots}.")
    if not defense_error_players.empty:
        with st.expander("Who makes their errors"):
            st.dataframe(
                defense_error_players,
                hide_index=True,
                use_container_width=True,
                column_config=column_config_for(defense_error_players),
            )

st.markdown("##### Can we run on them?")
opponent_catchers = team_catcher_throwing(league_season_id, opponent)
league_cs = league_catcher_throwing(league_season_id)
if opponent_catchers.empty:
    st.info("No stolen-base attempts recorded against this opponent's catchers.")
else:
    st.dataframe(
        opponent_catchers,
        hide_index=True,
        use_container_width=True,
        column_config=column_config_for(opponent_catchers),
    )
    primary = opponent_catchers.iloc[0]
    if primary["cs_pct"] is not None and league_cs and league_cs["cs_pct"] is not None:
        verdict = "Run at will" if primary["cs_pct"] < league_cs["cs_pct"] else "Be selective"
        st.markdown(
            f"**{verdict}:** their main catcher **{primary['player']}** has thrown out "
            f"**{primary['cs_pct']:.1%}** of runners ({int(primary['cs'])} of "
            f"{int(primary['sb_att'])} attempts), against a league average of "
            f"**{league_cs['cs_pct']:.1%}**."
        )
    st.caption(
        "Attempts are steals allowed plus runners caught. Part of a team's steals allowed is "
        "charged to the pitcher in this league's scoring, so these are the catcher's own share; "
        "a slow-working pitcher inflates a catcher's steals against."
    )

st.divider()

# --------------------------------------------------------------------------
# Our lineup
# --------------------------------------------------------------------------

st.subheader(f"{our_team}: recommended lineup")
roster = team_roster(league_season_id)
our_players = roster[roster["team"] == our_team]["player"].tolist()
available = st.multiselect(
    "Available this week (select everyone at the game — the best 9 bats start, the rest are ranked "
    "as options off the bench)",
    our_players,
    default=our_players,
    key="scout_available",
)

lineup_data = None
if len(available) < 2:
    st.info("Select the hitters who are available this week.")
else:
    if len(available) < 9:
        st.warning(f"Only {len(available)} hitters available — all of them are in the lineup.")
    with st.spinner("Optimizing batting order…"):
        lineup_data = lineup_recommendation(league_season_id, our_team, available, vs_throws)
    result = lineup_data["result"]
    st.markdown(
        f"**Starting nine — {result.expected_runs:.2f} expected runs** per 7-inning game "
        f"(same nine batted best-to-worst instead: {result.baselines['by_woba_desc']:.2f})."
    )
    lineup_table = lineup_data["lineup"]
    st.dataframe(
        lineup_table, hide_index=True, use_container_width=True, column_config=column_config_for(lineup_table)
    )
    st.markdown("**Why each hitter is where they are**")
    for line in result.rationale:
        st.markdown(line)
    st.caption(
        "Order differences are worth fractions of a run per game — a tiebreaker between defensible "
        "orders, not a verdict. Positions are yours to assign."
    )
    bench = lineup_data["bench"]
    if not bench.empty:
        st.markdown("**Bench**")
        st.dataframe(bench, hide_index=True, use_container_width=True, column_config=column_config_for(bench))
        st.caption(
            "Pinch-hit calls compare each bench bat's record against left- and right-handed pitching, "
            "ranked on a lower confidence bound so a projection with more evidence behind it wins a "
            "tie. Hitters under 20 PA aren't *named* as an option — a handful of PA can't support "
            "that call — but their projection and its margin are still shown, because a thin sample "
            "is better information than none."
        )
    with st.expander("Under the hood: what the model believed about each hitter"):
        profiles = lineup_data["profiles"]
        st.dataframe(
            profiles, hide_index=True, use_container_width=True, column_config=column_config_for(profiles)
        )

st.divider()

# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

st.subheader("Full report (PDF)")
st.caption(
    f"Opponent overview, all their hitters with spray-chart detail for the top {PDF_TOP_HITTERS}, "
    f"probable pitchers with your batters' history against them, and the lineup above."
)

if st.button("Generate PDF", type="primary"):
    with st.spinner("Building report (rendering charts takes a few seconds)…"):
        seasons = list_league_seasons()
        row = seasons[seasons["league_season_id"] == league_season_id].iloc[0]
        league_label = f"{row['league_name']} ({row['year']})"

        team_stats = team_season_stats(league_season_id)
        overview = pd.DataFrame()
        if not team_stats.empty:
            opponent_row = team_stats[team_stats["team"] == opponent]
            league_avg = team_stats.mean(numeric_only=True).to_dict()
            league_avg["team"] = "League average"
            overview = pd.concat([opponent_row, pd.DataFrame([league_avg])], ignore_index=True)

        hitter_details = []
        detail_rows = hitters[hitters["pa"] >= MIN_PA_FOR_DETAIL].head(PDF_TOP_HITTERS)
        for _, h in detail_rows.iterrows():
            tendency = batter_tendency(h["player"], league_season_id)
            note_bits = []
            if h["bats"] in ("L", "R", "S"):
                note_bits.append({"L": "Bats left", "R": "Bats right", "S": "Switch hitter"}[h["bats"]])
            if tendency:
                total = tendency["pull"] + tendency["center"] + tendency["oppo"]
                note_bits.append(
                    f"{tendency['tendency_label']}-leaning ({tendency['pull']}/{tendency['center']}/"
                    f"{tendency['oppo']} pull/center/oppo of {total} tracked balls in play)"
                )
            if pd.notna(h["k_pct"]):
                note_bits.append(f"strikes out in {h['k_pct']:.0%} of PA")
            hitter_details.append(
                {
                    "name": h["player"],
                    "note": "; ".join(note_bits) + "." if note_bits else None,
                    "season_points": batter_spray_points(h["player"], league_season_id),
                    "career_points": batter_spray_points(h["player"]),
                }
            )

        pitcher_details = []
        if probables is not None and not probables.empty:
            for _, p in probables.iterrows():
                pitcher_details.append(
                    {
                        "name": p["player"],
                        "throws": p["throws"],
                        "evidence": p["evidence"] if pd.notna(p["evidence"]) else None,
                        "spray_points": pitcher_spray_points(p["player"]),
                        "vs_hands": pitcher_vs_hands(p["player"]),
                        "matchups": roster_vs_pitcher(league_season_id, our_team, p["player"]),
                    }
                )

        pdf_bytes = build_scouting_pdf(
            {
                "our_team": our_team,
                "opponent": opponent,
                "league_label": league_label,
                "freshness": data_freshness(),
                "fixture": next_meeting,
                "standings": standings(league_season_id),
                "team_stats": overview,
                "recent_games": team_recent_games(league_season_id, opponent),
                "hitters": hitters,
                "hitter_details": hitter_details,
                "defense": opponent_defense,
                "defense_error_players": defense_error_players,
                "catchers": opponent_catchers,
                "league_catcher_cs_pct": (league_cs or {}).get("cs_pct"),
                "staff": staff,
                "pitcher_details": pitcher_details,
                "lineup": (
                    {
                        "result": lineup_data["result"],
                        "lineup": lineup_data["lineup"],
                        "bench": lineup_data["bench"],
                        "vs_throws": vs_throws,
                    }
                    if lineup_data
                    else None
                ),
            }
        )
    st.download_button(
        "Download scouting report",
        data=pdf_bytes,
        file_name=f"Scouting Report - {opponent}.pdf",
        mime="application/pdf",
    )
