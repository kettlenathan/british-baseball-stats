import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.charts import scatter_chart
from app.components.data_access import batter_archetype_k_diagnostics, batter_archetypes
from app.components.filters import league_season_selector
from app.components.formatting import (
    ARCHETYPE_COLUMN_CONFIG,
    ARCHETYPE_DIAGNOSTICS_COLUMN_CONFIG,
    ARCHETYPE_PROFILE_COLUMN_CONFIG,
)
from stats.archetypes import cluster_profile

# One well-known MLB career per feature pair, picked for whichever direction
# (High/Low) has the clearest historical match — not exhaustive over every
# possible signed combination a fitted cluster label could show (see
# Methodology page), just a reference point for what each *dimension* of
# the archetype space looks like in a real, recognizable career. Career
# totals below are verified against Baseball-Reference/Baseball Almanac;
# BB%/K% are approximate (based on public career-rate reporting, not
# recomputed from this app's own PA-based formula).
MLB_ARCHETYPE_EXAMPLES = [
    {
        "archetype": "High Net Pull%, High BB%",
        "player": "Ted Williams",
        "why": (
            "Pulled almost everything to right field — the 1946 \"Boudreau Shift\" (three "
            "infielders stacked on the right side) was built specifically to counter it — while "
            "also posting the highest career walk rate of any hitter ever (~21% of PA)."
        ),
    },
    {
        "archetype": "High Net Pull%, Low K%",
        "player": "George Brett",
        "why": (
            "317 career home runs on a career strikeout rate under 8% — a rare power/contact "
            "blend built on a classic pull-side line-drive swing."
        ),
    },
    {
        "archetype": "High Net Pull%, High 2B%",
        "player": "David Ortiz",
        "why": (
            "One of the most heavily shifted hitters of the shift-tracking era (defenses stacked "
            "the right side against his dead-pull power), while also racking up the 12th-most "
            "doubles in MLB history (632)."
        ),
    },
    {
        "archetype": "High Net Pull%, High 3B%",
        "player": "Carlos Beltran",
        "why": (
            "This pairing is genuinely rare in MLB history — triples usually come from gap/"
            "opposite-field speed, which cuts against a pull-heavy approach. Beltran is the "
            "closest well-known fit: a switch-hitter famous for a 300 HR/300 SB combination, "
            "even though his triples total (78) wasn't itself a standout number."
        ),
    },
    {
        "archetype": "High Net Pull%, High HR%",
        "player": "Ryan Howard",
        "why": (
            "One of the most extreme pull hitters ever tracked (pulled far more ground balls "
            "than a typical hitter) and shifted against in the large majority of his plate "
            "appearances at his peak, alongside 382 career home runs including a 58-HR MVP season."
        ),
    },
    {
        "archetype": "High BB%, Low K%",
        "player": "Joey Votto",
        "why": (
            "Repeatedly led MLB in walk rate over his career while striking out far less often "
            "than most sluggers of his power level — the modern model of plate discipline."
        ),
    },
    {
        "archetype": "High BB%, High 2B%",
        "player": "Edgar Martinez",
        "why": (
            "A .418 career OBP paired with 514 career doubles as a full-time DH — patient, "
            "gap-power hitting (Seattle's award for hitting excellence carries his name)."
        ),
    },
    {
        "archetype": "High BB%, High 3B%",
        "player": "Rickey Henderson",
        "why": (
            "2,190 career walks (top-5 all time) from a game built on speed and plate patience — "
            "though his signature stat is stolen bases (1,406, the all-time record) rather than "
            "triples specifically, so read this pairing as \"speed plus patience,\" not a pure "
            "triples showcase."
        ),
    },
    {
        "archetype": "High BB%, High HR%",
        "player": "Barry Bonds",
        "why": "Holds the all-time record in both categories at once: 762 home runs and 2,558 walks.",
    },
    {
        "archetype": "Low K%, High 2B%",
        "player": "Wade Boggs",
        "why": (
            "Five batting titles on a ~7% career strikeout rate, plus 578 doubles (top-15 all "
            "time) — elite bat control paired with gap power."
        ),
    },
    {
        "archetype": "Low K%, High 3B%",
        "player": "Ichiro Suzuki",
        "why": "A ~10% strikeout rate across 19 MLB seasons plus 96 career triples — contact and speed rather than power.",
    },
    {
        "archetype": "High K%, High HR%",
        "player": "Reggie Jackson",
        "why": (
            "Retired as MLB's all-time strikeout leader (2,597) while hitting 563 home runs — "
            "\"Mr. October\" swung big and missed big."
        ),
    },
    {
        "archetype": "High 2B%, High 3B%",
        "player": "Stan Musial",
        "why": (
            "725 career doubles (3rd all time) and 177 triples (top-20 all time) at once — unlike "
            "most pairings here, there's no real tension between the two totals."
        ),
    },
    {
        "archetype": "High 2B%, High HR%",
        "player": "Albert Pujols",
        "why": "Top-5 all time in career doubles (686) and top-5 all time in career home runs (703) simultaneously.",
    },
    {
        "archetype": "High 3B%, High HR%",
        "player": "Willie Mays",
        "why": (
            "140 career triples alongside 660 home runs — a combination only Babe Ruth (136/714) "
            "can also claim, the definitional five-tool profile."
        ),
    },
]

st.set_page_config(page_title="Batter Archetypes", page_icon="🧬", layout="wide")
st.title("Batter Archetypes")
st.caption(
    "Unsupervised clustering (k-means) of batters into descriptive archetypes, using net pull "
    "tendency, BB%/K%, and extra-base-hit mix — an exploratory grouping, not a fixed stat. ISO, "
    "raw Pull%/Oppo%/Center%, and 1B% are shown for context below but deliberately left out of "
    "the clustering itself, since they'd double-count a signal another feature already captures "
    "— see the Methodology page."
)

league_season_id = league_season_selector()
if league_season_id is None:
    st.stop()

min_pa = st.slider("Minimum PA", 1, 100, 20)

MIN_QUALIFYING_BATTERS = 10

df = batter_archetypes(league_season_id, min_pa=min_pa)
if len(df) < MIN_QUALIFYING_BATTERS:
    st.info(
        f"Only {len(df)} qualifying batter(s) at this PA threshold — need at least "
        f"{MIN_QUALIFYING_BATTERS} to cluster meaningfully. Try lowering the minimum PA."
    )
    st.stop()

fig = scatter_chart(df, x="pc1", y="pc2", color_col="cluster_label", hover_name="player")
fig.update_xaxes(title=df["pc1_label"].iloc[0])
fig.update_yaxes(title=df["pc2_label"].iloc[0])
st.plotly_chart(fig, use_container_width=True)
st.caption(
    "Clustering runs on the full standardized 6-feature space; this scatter is a 2-component "
    "PCA projection for visualization only. Each axis is labeled by the features that dominate "
    "it — position along an axis is relative, not an absolute stat value."
)

highlight = st.multiselect("Highlight players", sorted(df["player"]))

with st.expander("How k was chosen"):
    diagnostics_df = batter_archetype_k_diagnostics(league_season_id, min_pa=min_pa)
    st.caption(
        "k is chosen automatically by maximizing mean silhouette score across candidate values "
        "— not a hardcoded guess."
    )
    st.dataframe(
        diagnostics_df,
        hide_index=True,
        use_container_width=True,
        column_config=ARCHETYPE_DIAGNOSTICS_COLUMN_CONFIG,
    )

with st.expander("Cluster profile"):
    st.caption(
        "Averaged over every stat shown on this page, including ones not used for clustering "
        "itself (ISO, raw Pull%/Oppo%/Center%, 1B%) — useful for describing a cluster even "
        "though they weren't part of what grouped it."
    )
    profile_columns = [
        "pull_pct", "center_pct", "oppo_pct", "pull_minus_oppo", "iso", "bb_pct", "k_pct",
        "singles_pct", "doubles_pct", "triples_pct", "hr_pct",
    ]
    st.dataframe(
        cluster_profile(df, columns=profile_columns),
        hide_index=True,
        use_container_width=True,
        column_config=ARCHETYPE_PROFILE_COLUMN_CONFIG,
    )

with st.expander("Underlying data"):
    shown = df[df["player"].isin(highlight)] if highlight else df
    st.dataframe(
        shown.sort_values("cluster_label"),
        hide_index=True,
        use_container_width=True,
        column_config=ARCHETYPE_COLUMN_CONFIG,
    )

with st.expander("MLB comparisons: what do these archetypes look like in the majors?"):
    st.caption(
        "One well-known MLB career per feature pair, picked for whichever direction has the "
        "clearest historical match — a reference point for the archetype space in general, not "
        "necessarily matching the specific cluster labels above for this league-season."
    )
    for example in MLB_ARCHETYPE_EXAMPLES:
        st.markdown(f"**{example['archetype']}** — *{example['player']}*")
        st.caption(example["why"])
