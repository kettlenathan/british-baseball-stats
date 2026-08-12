import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.charts import scatter_chart
from app.components.data_access import (
    batting_leaderboard,
    batting_true_talent,
    division_environments,
    pitching_leaderboard,
    pitching_true_talent,
    season_progress,
    standings,
)
from app.components.filters import (
    division_selector,
    filter_by_division,
    league_season_selector,
)
from app.components.formatting import (
    BATTING_COLUMN_CONFIG,
    PCT_COLUMN_CONFIG,
    PITCHING_COLUMN_CONFIG,
    TRUE_TALENT_BATTING_COLUMN_CONFIG,
    TRUE_TALENT_PITCHING_COLUMN_CONFIG,
)
from stats.war import WAR_DISCLAIMER

st.set_page_config(page_title="Leaderboards", page_icon="⚾", layout="wide")
st.title("Leaderboards")

league_season_id = league_season_selector()
if league_season_id is None:
    st.stop()

division = division_selector(league_season_id)

tab_batting, tab_pitching, tab_standings = st.tabs(["Batting", "Pitching", "Standings"])

# Shown wherever both baselines appear together. The distinction is the
# whole reason two columns exist, and without it a reader will assume the
# smaller number is a correction of the larger one.
_BASELINE_CAPTION = (
    "**wRC+** compares a hitter to the whole competition; **wRC+ vs Div** compares "
    "them to their own division. Neither is the truer number — teams only play "
    "inside their division, so the first measures them against opponents they never "
    "faced, while the second measures them against a bar that may itself be low. "
    "A big gap between the two means the division's run environment is unusual, not "
    "that the player is over- or under-rated. Reading both together is the most reliable "
    "way to compare players across divisions; the Division Strength page shows how far "
    "apart the divisions were."
)
_BASELINE_CAPTION_PITCHING = _BASELINE_CAPTION.replace("wRC+", "ERA+").replace(
    "a hitter", "a pitcher"
).replace("the player", "the pitcher")

with tab_batting:
    min_pa = st.slider("Minimum PA", 0, 100, 10, key="min_pa")
    df = filter_by_division(batting_leaderboard(league_season_id, min_pa=min_pa), division)
    if df.empty:
        st.info("No qualifying batters.")
    else:
        st.dataframe(
            df.sort_values("war", ascending=False),
            hide_index=True,
            use_container_width=True,
            column_config=BATTING_COLUMN_CONFIG,
        )
        if df["wrc_plus_div"].notna().any():
            st.caption(_BASELINE_CAPTION)
        st.caption(WAR_DISCLAIMER)

    with st.expander("True talent (empirical-Bayes shrinkage)"):
        tt_df = batting_true_talent(league_season_id, min_pa=1)
        if tt_df.empty:
            st.info("No qualifying batters.")
        else:
            st.dataframe(
                tt_df.sort_values("shrunk_woba", ascending=False),
                hide_index=True,
                use_container_width=True,
                column_config=TRUE_TALENT_BATTING_COLUMN_CONFIG,
            )
            st.plotly_chart(
                scatter_chart(tt_df, x="pa", y="shrunk_woba", color_col="team"),
                use_container_width=True,
            )
            st.caption(
                "Shrinks each batter's observed wOBA toward the league-season mean, "
                "weighted by PA — low-PA players regress heavily toward the league "
                "average since a handful of at-bats is mostly sampling noise. See "
                "the Methodology page for the full formula."
            )

with tab_pitching:
    min_ip = st.slider("Minimum IP", 0, 60, 5, key="min_ip")
    df = filter_by_division(pitching_leaderboard(league_season_id, min_ip=min_ip), division)
    if df.empty:
        st.info("No qualifying pitchers.")
    else:
        st.dataframe(
            df.sort_values("war", ascending=False),
            hide_index=True,
            use_container_width=True,
            column_config=PITCHING_COLUMN_CONFIG,
        )
        if df["era_plus_div"].notna().any():
            st.caption(_BASELINE_CAPTION_PITCHING)
        st.caption(WAR_DISCLAIMER)

    with st.expander("True talent (empirical-Bayes shrinkage)"):
        tt_df = pitching_true_talent(league_season_id, min_ip=0.1)
        if tt_df.empty:
            st.info("No qualifying pitchers.")
        else:
            st.dataframe(
                tt_df.sort_values("shrunk_fip"),
                hide_index=True,
                use_container_width=True,
                column_config=TRUE_TALENT_PITCHING_COLUMN_CONFIG,
            )
            st.plotly_chart(
                scatter_chart(tt_df, x="ip", y="shrunk_fip", color_col="team"),
                use_container_width=True,
            )
            st.caption(
                "Shrinks each pitcher's observed FIP toward the league-season mean, "
                "weighted by IP — low-IP pitchers regress heavily toward the league "
                "average since a handful of innings is mostly sampling noise. See "
                "the Methodology page for the full formula."
            )

with tab_standings:
    progress = season_progress(league_season_id)
    if not progress["complete"] and progress["total"]:
        st.info(
            f"**Season in progress** — {progress['played']} of {progress['total']} league "
            f"fixtures played ({progress['pct_complete']:.0%}). The full schedule is "
            "published in advance, so these are standings to date, not final placings."
        )

    df = standings(league_season_id)
    if df.empty:
        st.info("No completed games yet.")
    elif df["division"].notna().any():
        # One table per division, as the site itself publishes it. A single
        # combined table would rank teams whose schedules never overlapped.
        st.caption(
            "Regular-season records only. Teams play only within their own division, "
            "so records in different blocks were built against different opposition "
            "and are not directly comparable."
        )
        if "rating" in df.columns and df["rating"].notna().any():
            st.caption(
                "**Rating** is a Bradley-Terry strength estimate that accounts for who each "
                "team actually played, on a log-odds scale where 0 is a division-average "
                "team. **SOS** is how much harder the schedule was than an even draw would "
                "have been — negative means an easier run. Both are comparable *within* a "
                "division only; nothing here compares one division to another."
            )
        for name in df["division"].dropna().unique():
            st.subheader(name)
            st.dataframe(
                df[df["division"] == name].drop(columns=["division"]),
                hide_index=True,
                use_container_width=True,
                column_config=PCT_COLUMN_CONFIG,
            )
        unplaced = df[df["division"].isna()]
        if not unplaced.empty:
            # Surfaced rather than dropped, so the teams still add up — the
            # same principle as the "UNK" fielding position.
            st.subheader("Not in a published division")
            st.dataframe(
                unplaced.drop(columns=["division"]),
                hide_index=True,
                use_container_width=True,
                column_config=PCT_COLUMN_CONFIG,
            )

        env = division_environments(league_season_id)
        if not env.empty:
            with st.expander("How the divisions compare as run environments"):
                st.dataframe(
                    env, hide_index=True, use_container_width=True,
                    column_config=PCT_COLUMN_CONFIG,
                )
                st.caption(
                    "Scoring levels differ sharply between divisions of the same league, "
                    "which is why the leaderboards carry a division-relative column "
                    "alongside the league-wide one. This table cannot tell you which "
                    "division is *stronger* — a low-scoring division may have better "
                    "pitching or weaker hitting, and these games alone cannot separate "
                    "the two."
                )
    else:
        st.dataframe(
            df.drop(columns=["division"]),
            hide_index=True,
            use_container_width=True,
            column_config=PCT_COLUMN_CONFIG,
        )
