import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.charts import spray_chart, spray_heatmap, trend_chart
from app.components.data_access import (
    all_player_names,
    batter_pitcher_matchups_career,
    batter_pitcher_matchups_season,
    batter_spray_points,
    batter_tendency,
    batting_true_talent,
    pitcher_spray_points,
    pitching_true_talent,
    player_batting_career,
    player_league_seasons,
    player_pitching_career,
)
from app.components.formatting import BATTING_COLUMN_CONFIG, MATCHUP_COLUMN_CONFIG, PITCHING_COLUMN_CONFIG

st.set_page_config(page_title="Player Page", page_icon="🧑‍💼", layout="wide")
st.title("Player Page")

names = all_player_names()
if not names:
    st.info("No players scraped yet.")
    st.stop()

player = st.selectbox("Player", names)
bat_df = player_batting_career(player)
pitch_df = player_pitching_career(player)

if bat_df.empty and pitch_df.empty:
    st.info("No seasons found for this player.")
    st.stop()

seasons_df = player_league_seasons(player)
_SCOPE_OPTIONS = ["Career"] + [
    f"{row.year} {row.league.upper()}" for row in seasons_df.itertuples()
]


def _scope_selector(key: str) -> tuple[int | None, str]:
    """Lets the user pick "Career" (across every league/season the player
    has appeared in) or one specific league_season to scope the batted-ball
    tendency, spray chart, and matchups sections below."""
    choice = st.radio("Scope", _SCOPE_OPTIONS, horizontal=True, key=key)
    if choice == "Career":
        return None, choice
    league_season_id = int(seasons_df.iloc[_SCOPE_OPTIONS.index(choice) - 1]["league_season_id"])
    return league_season_id, choice


if not bat_df.empty:
    st.subheader(f"{player} — career batting")
    st.dataframe(
        bat_df,
        hide_index=True,
        use_container_width=True,
        column_config=BATTING_COLUMN_CONFIG,
    )
    if len(bat_df) > 1:
        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(trend_chart(bat_df, "year", "ops"), use_container_width=True)
        with col2:
            st.plotly_chart(trend_chart(bat_df, "year", "war"), use_container_width=True)

    bat_scope_id, bat_scope_label = _scope_selector("bat_scope")

    st.markdown("##### True talent (empirical-Bayes shrinkage)")
    if bat_scope_id is None:
        st.info(
            "Select a specific season above to see shrinkage-adjusted wOBA — it shrinks "
            "toward that season's own league mean, so it isn't defined at the career level."
        )
    else:
        player_tt = batting_true_talent(bat_scope_id)
        player_tt = player_tt[player_tt["player"] == player]
        if player_tt.empty:
            st.info("No true-talent estimate available for this season.")
        else:
            row = player_tt.iloc[0]
            st.metric(
                "True Talent wOBA",
                f"{row['shrunk_woba']:.3f}",
                help=(
                    f"Observed {row['observed_woba']:.3f} over {int(row['pa'])} PA "
                    f"(reliability {row['reliability']:.0%})"
                ),
            )

    st.markdown("##### Batted-ball tendency")
    tendency = batter_tendency(player, bat_scope_id)
    if tendency is None:
        st.info(
            "No pull/center/oppo tendency available for this selection — either no batted-ball "
            "data, or a switch hitter (excluded, since there's no per-plate-appearance record of "
            "which side they batted from)."
        )
    else:
        st.caption(
            f"{bat_scope_label}: **{tendency['tendency_label'].title()} hitter** — "
            f"Pull {tendency['pull']} / Center {tendency['center']} / Oppo {tendency['oppo']}"
        )
        hand_choice = st.radio("Vs.", ["All", "vs LHP", "vs RHP"], horizontal=True, key="bat_hand")
        vs_hand = {"All": None, "vs LHP": "L", "vs RHP": "R"}[hand_choice]
        spray_df = batter_spray_points(player, bat_scope_id, vs_hand)
        if spray_df.empty:
            st.info("No batted-ball data for this selection.")
        else:
            spray_col, heatmap_col = st.columns(2)
            with spray_col:
                st.plotly_chart(spray_chart(spray_df), use_container_width=True)
            with heatmap_col:
                st.plotly_chart(spray_heatmap(spray_df), use_container_width=True)
                st.caption("Direction only — distance is dropped since it's the less reliable field.")

    with st.expander("Matchups vs. pitchers faced"):
        matchup_df = (
            batter_pitcher_matchups_season(player, True, bat_scope_id)
            if bat_scope_id is not None
            else batter_pitcher_matchups_career(player, True)
        )
        if matchup_df.empty:
            st.info("No matchup data available.")
        else:
            st.dataframe(matchup_df, hide_index=True, use_container_width=True, column_config=MATCHUP_COLUMN_CONFIG)

if not pitch_df.empty:
    st.subheader(f"{player} — career pitching")
    st.dataframe(
        pitch_df,
        hide_index=True,
        use_container_width=True,
        column_config=PITCHING_COLUMN_CONFIG,
    )
    if len(pitch_df) > 1:
        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(trend_chart(pitch_df, "year", "era"), use_container_width=True)
        with col2:
            st.plotly_chart(trend_chart(pitch_df, "year", "war"), use_container_width=True)

    pitch_scope_id, pitch_scope_label = _scope_selector("pitch_scope")

    st.markdown("##### True talent (empirical-Bayes shrinkage)")
    if pitch_scope_id is None:
        st.info(
            "Select a specific season above to see shrinkage-adjusted FIP — it shrinks "
            "toward that season's own league mean, so it isn't defined at the career level."
        )
    else:
        player_tt = pitching_true_talent(pitch_scope_id)
        player_tt = player_tt[player_tt["player"] == player]
        if player_tt.empty:
            st.info("No true-talent estimate available for this season.")
        else:
            row = player_tt.iloc[0]
            st.metric(
                "True Talent FIP",
                f"{row['shrunk_fip']:.2f}",
                help=(
                    f"Observed {row['observed_fip']:.2f} over {row['ip']:.1f} IP "
                    f"(reliability {row['reliability']:.0%})"
                ),
            )

    st.markdown("##### Spray chart against")
    hand_choice = st.radio("Vs.", ["All", "vs LHB", "vs RHB"], horizontal=True, key="pitch_hand")
    vs_hand = {"All": None, "vs LHB": "L", "vs RHB": "R"}[hand_choice]
    pitch_spray_df = pitcher_spray_points(player, pitch_scope_id, vs_hand)
    if pitch_spray_df.empty:
        st.info("No batted-ball data allowed for this selection.")
    else:
        spray_col, heatmap_col = st.columns(2)
        with spray_col:
            st.plotly_chart(spray_chart(pitch_spray_df), use_container_width=True)
            st.caption(f"{pitch_scope_label} — balls in play allowed.")
        with heatmap_col:
            st.plotly_chart(spray_heatmap(pitch_spray_df), use_container_width=True)
            st.caption("Direction only — distance is dropped since it's the less reliable field.")

    with st.expander("Matchups vs. batters faced"):
        matchup_df = (
            batter_pitcher_matchups_season(player, False, pitch_scope_id)
            if pitch_scope_id is not None
            else batter_pitcher_matchups_career(player, False)
        )
        if matchup_df.empty:
            st.info("No matchup data available.")
        else:
            st.dataframe(matchup_df, hide_index=True, use_container_width=True, column_config=MATCHUP_COLUMN_CONFIG)
