import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.charts import bar_chart
from app.components.data_access import (
    batting_leaderboard,
    league_catcher_throwing,
    league_fielding_by_position,
    pitching_leaderboard,
    team_catcher_throwing,
    team_fielding_by_position,
    team_position_error_players,
    team_division,
    team_recent_games,
    team_roster,
    team_season_stats,
)
from app.components.filters import league_season_selector
from app.components.formatting import (
    BATTING_COLUMN_CONFIG,
    CATCHER_THROWING_COLUMN_CONFIG,
    FIELDING_COLUMN_CONFIG,
    PITCHING_COLUMN_CONFIG,
    RECENT_GAMES_COLUMN_CONFIG,
    ROSTER_COLUMN_CONFIG,
    TEAM_COLUMN_CONFIG,
)

st.set_page_config(page_title="Team Page", page_icon="🏟️", layout="wide")
st.title("Team Page")

league_season_id = league_season_selector()
if league_season_id is None:
    st.stop()

roster_df = team_roster(league_season_id)
if roster_df.empty:
    st.info("No rosters scraped for this league/season yet.")
    st.stop()

teams = sorted(roster_df["team"].unique())
team = st.selectbox("Team", teams)

division_info = team_division(league_season_id, team)
if division_info:
    placing = (
        f"{division_info['rank']} of {division_info['of']}"
        if division_info["rank"]
        else "unplaced"
    )
    st.caption(
        f"**{division_info['division']}** division — {placing} on regular-season record. "
        "Every league game below was played inside this division, so the record and "
        "rate stats describe this opposition only and are not comparable with another "
        "division's without adjustment."
    )

    if division_info.get("rating") is not None:
        rating_col, sos_col, expected_col = st.columns(3)
        rating_col.metric(
            "Strength rating",
            f"{division_info['rating']:+.2f}",
            help=(
                "Bradley-Terry estimate accounting for who this team actually played. "
                "0 is a division-average team. Comparable within this division only."
            ),
        )
        sos = division_info["sos"]
        sos_col.metric(
            "Strength of schedule",
            f"{sos:+.2f}",
            # Framed as easier/harder rather than good/bad: a soft draw is not
            # a failing, and Streamlit's own red/green delta arrows would imply
            # a judgement the number doesn't carry.
            delta="harder draw" if sos > 0 else "easier draw",
            delta_color="off",
            help=(
                "How much stronger the opponents faced were than an even draw inside this "
                "division would have given. Negative means an easier run than the "
                "schedule alone suggests."
            ),
        )
        expected_col.metric(
            "Expected win %",
            f"{division_info['expected_win_pct']:.1%}",
            help="Against an average opponent from this division, at a neutral venue.",
        )

        left = division_info.get("games_remaining") or 0
        if left:
            run_in = division_info.get("sos_remaining")
            run_in_text = (
                f" Their remaining opponents rate {run_in:+.2f} against an even draw"
                f" — {'a harder' if run_in > 0 else 'an easier'} run-in than the season so far."
                if run_in is not None
                else ""
            )
            st.caption(
                f"⚠️ **Season in progress** — {left} league fixture"
                f"{'s' if left != 1 else ''} still to play, so the record and rating above "
                f"are a snapshot rather than a final standing.{run_in_text}"
            )

st.subheader("Team stats")
stats_df = team_season_stats(league_season_id)
team_stats_row = stats_df[stats_df["team"] == team]
if team_stats_row.empty:
    st.info("No combined stats available for this team yet.")
else:
    st.dataframe(
        team_stats_row,
        hide_index=True,
        use_container_width=True,
        column_config=TEAM_COLUMN_CONFIG,
    )

st.subheader("Recent performance")
recent_df = team_recent_games(league_season_id, team, weeks=3)
if recent_df.empty:
    st.info("No games in the last 3 weekends for this team.")
else:
    st.dataframe(
        recent_df,
        hide_index=True,
        use_container_width=True,
        column_config=RECENT_GAMES_COLUMN_CONFIG,
    )

st.subheader("Fielding by position")
fielding_df = team_fielding_by_position(league_season_id, team)
if fielding_df.empty:
    st.info("No fielding data recorded for this team yet.")
else:
    league_fielding = league_fielding_by_position(league_season_id)
    shown = fielding_df
    if not league_fielding.empty and "e_per_team" in league_fielding.columns:
        # Errors are only readable against the same position elsewhere in the
        # league — shortstops and third basemen out-error corner outfielders
        # everywhere, so "8 errors" means nothing without this column.
        shown = fielding_df.merge(
            league_fielding[["position", "e_per_team"]], on="position", how="left"
        )
        shown["e_vs_league"] = shown["e"] - shown["e_per_team"]

    table_col, chart_col = st.columns([3, 2])
    with table_col:
        st.dataframe(
            shown, hide_index=True, use_container_width=True, column_config=FIELDING_COLUMN_CONFIG
        )
        st.caption(
            "E vs League compares this team to the average team's errors at the *same* position. "
            "Fielding % is shown for context but rewards limited range — a fielder who never "
            "reaches a ball never misplays it."
        )
    with chart_col:
        with_errors = fielding_df[fielding_df["e"] > 0]
        if with_errors.empty:
            st.info("No errors recorded at any position.")
        else:
            st.plotly_chart(bar_chart(with_errors, "position", "e"), use_container_width=True)

    error_players = team_position_error_players(league_season_id, team)
    if not error_players.empty:
        with st.expander("Who made them"):
            st.dataframe(
                error_players,
                hide_index=True,
                use_container_width=True,
                column_config=FIELDING_COLUMN_CONFIG,
            )

    st.markdown("##### Catchers: throwing")
    catchers_df = team_catcher_throwing(league_season_id, team)
    if catchers_df.empty:
        st.info("No stolen-base attempts recorded against this team's catchers.")
    else:
        st.dataframe(
            catchers_df,
            hide_index=True,
            use_container_width=True,
            column_config=CATCHER_THROWING_COLUMN_CONFIG,
        )
        league_cs = league_catcher_throwing(league_season_id)
        league_note = (
            f" League-wide, catchers throw out **{league_cs['cs_pct']:.1%}** of runners "
            f"({league_cs['cs']:,} of {league_cs['sb_att']:,} attempts)."
            if league_cs and league_cs["cs_pct"] is not None
            else ""
        )
        st.caption(
            "Attempts are steals allowed plus runners caught. Note this league's scorers charge "
            "part of a team's steals allowed to the **pitcher**, so these are the catcher's own "
            "share rather than every steal the team gave up — and a catcher's CS% depends heavily "
            "on how quickly their pitchers work." + league_note
        )

    if "UNK" in set(fielding_df["position"]):
        unknown = int(fielding_df.loc[fielding_df["position"] == "UNK", "e"].iloc[0])
        st.caption(
            f"{unknown} error(s) shown as UNK: the box score recorded them against a player who "
            "changed position mid-game, and the play-by-play didn't name which position. They're "
            "listed rather than dropped so the positions still add up to the team's real total."
        )

st.subheader("Roster")
st.dataframe(
    roster_df[roster_df["team"] == team].drop(columns=["team"]),
    hide_index=True,
    use_container_width=True,
    column_config=ROSTER_COLUMN_CONFIG,
)

col1, col2 = st.columns(2)
with col1:
    st.subheader("Batting")
    bat_df = batting_leaderboard(league_season_id, min_pa=0)
    st.dataframe(
        bat_df[bat_df["team"] == team].sort_values("pa", ascending=False),
        hide_index=True,
        use_container_width=True,
        column_config=BATTING_COLUMN_CONFIG,
    )
with col2:
    st.subheader("Pitching")
    pitch_df = pitching_leaderboard(league_season_id, min_ip=0)
    st.dataframe(
        pitch_df[pitch_df["team"] == team].sort_values("ip", ascending=False),
        hide_index=True,
        use_container_width=True,
        column_config=PITCHING_COLUMN_CONFIG,
    )
