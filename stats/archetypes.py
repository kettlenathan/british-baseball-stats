"""Unsupervised clustering of batters into descriptive archetypes, using
spray tendency, plate discipline, and extra-base-hit mix.

Computed at read time, not materialized: unlike wOBA/WAR, clustering depends
on user-adjustable parameters (population scope, k) with no single "correct"
fixed value the way a linear-weight formula does. Feature engineering (the
values being clustered) still lives entirely in stats/ — this module and
stats/rate_stats.py's batting_rate_stats/hit_type_mix; only the model fit
itself happens here, invoked from a cached app/components/data_access.py
function, so app/ pages never import sklearn directly.

Batted-ball type (ground/fly/line/pop, derived from PlateAppearance.hittype)
is deliberately excluded from the feature set — hitdistance, which any
attempt to interpret hittype would lean on, is known to be unreliable in
this data source (most hits recorded with a placeholder distance rather than
a real value). The extra-base-hit mix below uses only already-reliable
counting stats instead.

FEATURE_COLUMNS deliberately excludes several values that data_access still
computes and displays for context, because they'd double-count a signal
already-present feature captures:
- `iso` is dropped — it's a weighted recombination of the same doubles/
  triples/HR events that doubles_pct/triples_pct/hr_pct already describe at
  finer granularity (which extra-base hit type, not just "how many"), so
  including both would let the power/contact axis dominate clustering
  distance simply by being represented twice.
- `singles_pct` is dropped — singles/doubles/triples/HR is compositional
  (shares sum to 1), so every category is a fixed linear function of the
  others in the group. Keeping all four adds no information, only
  collinearity; dropping one "remainder" category (the standard treatment
  for compositional features) leaves the same degrees of freedom without it.
- `pull_pct` and `oppo_pct` are dropped in favor of one combined
  `pull_minus_oppo` feature. Pull/center/oppo is also compositional, so
  center_pct is already excluded for the same reason as singles_pct above —
  but pull_pct and oppo_pct on their own still carry only one real axis of
  variation between them (how pulled a batter's contact is, pull-side vs.
  oppo-side), just split across two mirrored features instead of one signed
  one. Keeping both would double-count that single axis the same way ISO
  alongside the hit-type mix would.
"""

import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

FEATURE_COLUMNS = [
    "pull_minus_oppo",
    "bb_pct",
    "k_pct",
    "doubles_pct",
    "triples_pct",
    "hr_pct",
]

# Display names for _label_dominant_features — kept local to this module
# rather than reusing app/components/theme.py's stat_label, since stats/
# must not depend on app/ (see CLAUDE.md's one-direction pipeline layering).
_FEATURE_LABELS = {
    "pull_minus_oppo": "Net Pull%",
    "bb_pct": "BB%",
    "k_pct": "K%",
    "doubles_pct": "2B%",
    "triples_pct": "3B%",
    "hr_pct": "HR%",
}


def _prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    """Adds engineered features derived from raw inputs (see FEATURE_COLUMNS
    module docstring) without mutating the caller's df."""
    df = df.copy()
    df["pull_minus_oppo"] = df["pull_pct"] - df["oppo_pct"]
    return df


def _default_k_range(n_samples: int) -> range:
    max_k = min(8, n_samples // 5)
    return range(2, max(3, max_k + 1))


def select_k(X, k_range: range) -> tuple[int, pd.DataFrame]:
    """Fits KMeans for each k in k_range. Returns (best_k by max mean
    silhouette score, a diagnostics DataFrame of k/inertia/silhouette for
    display) — so "why this k" is visible rather than a hardcoded guess."""
    diagnostics = []
    for k in k_range:
        model = KMeans(n_clusters=k, n_init=10, random_state=42)
        labels = model.fit_predict(X)
        diagnostics.append({"k": k, "inertia": model.inertia_, "silhouette": silhouette_score(X, labels)})
    diagnostics_df = pd.DataFrame(diagnostics)
    best_k = int(diagnostics_df.loc[diagnostics_df["silhouette"].idxmax(), "k"])
    return best_k, diagnostics_df


def _label_dominant_features(weights: pd.Series, top_n: int = 2) -> str:
    """Formats the top_n |weight| features of a feature-indexed vector into
    a short descriptive label, e.g. "High HR%, Pull-heavy" — used both for a
    cluster centroid (in standardized-space) and for a PCA component's
    loadings, so both cluster labels and scatter-plot axis labels are
    generated from the actual fitted model rather than a fixed taxonomy or
    an opaque "Component 1"."""
    top_features = weights.abs().sort_values(ascending=False).index[:top_n]
    parts = []
    for feature in top_features:
        direction = "High" if weights[feature] > 0 else "Low"
        name = _FEATURE_LABELS.get(feature, feature)
        parts.append(f"{direction} {name}")
    return ", ".join(parts)


def fit_archetypes(df: pd.DataFrame, k: int | None = None, random_state: int = 42) -> pd.DataFrame:
    """df must have FEATURE_COLUMNS plus any identifying columns (player,
    team, ...) to carry through untouched. Standardizes FEATURE_COLUMNS,
    auto-selects k via select_k when k is None, fits KMeans in the full
    standardized feature space, and separately projects to 2D via PCA for
    visualization only — the clustering itself is not run on the
    PCA-reduced space, since with this few features there's no
    dimensionality problem to solve and clustering directly preserves
    genuine distances better. Adds cluster/cluster_label/pc1/pc2 columns,
    plus pc1_label/pc2_label (the same string repeated down the column)
    describing which features dominate each PCA axis, so a scatter plot
    doesn't need to show a bare "Component 1"/"Component 2"."""
    prepared = _prepare_features(df)
    X = StandardScaler().fit_transform(prepared[FEATURE_COLUMNS])

    if k is None:
        k, _ = select_k(X, _default_k_range(len(df)))

    model = KMeans(n_clusters=k, n_init=10, random_state=random_state)
    labels = model.fit_predict(X)

    centroids = pd.DataFrame(model.cluster_centers_, columns=FEATURE_COLUMNS)
    label_by_cluster = {i: _label_dominant_features(centroids.loc[i]) for i in range(k)}

    pca = PCA(n_components=2, random_state=random_state)
    pcs = pca.fit_transform(X)
    loadings = pd.DataFrame(pca.components_, columns=FEATURE_COLUMNS)
    pc1_label = _label_dominant_features(loadings.loc[0])
    pc2_label = _label_dominant_features(loadings.loc[1])

    result = df.copy()
    result["pull_minus_oppo"] = prepared["pull_minus_oppo"]
    result["cluster"] = labels
    result["cluster_label"] = [label_by_cluster[c] for c in labels]
    result["pc1"] = pcs[:, 0]
    result["pc2"] = pcs[:, 1]
    result["pc1_label"] = pc1_label
    result["pc2_label"] = pc2_label
    return result


def k_diagnostics(df: pd.DataFrame, k_range: range | None = None) -> pd.DataFrame:
    """Standalone silhouette/inertia diagnostics table for a range of k
    values, matching the same candidate range fit_archetypes would search by
    default — lets a caller show "why this k" without duplicating the
    standardization step by hand."""
    X = StandardScaler().fit_transform(_prepare_features(df)[FEATURE_COLUMNS])
    return select_k(X, k_range or _default_k_range(len(df)))[1]


def cluster_profile(df: pd.DataFrame, columns: list[str] | None = None) -> pd.DataFrame:
    """Per-cluster mean of `columns` (defaults to the clustering inputs,
    FEATURE_COLUMNS) plus a player count, for a summary table alongside the
    scatter plot. Pass a broader column list (e.g. including iso, which
    isn't itself a clustering input — see module docstring) to describe
    clusters more richly than what they were actually clustered on. df must
    already have cluster/cluster_label columns (i.e. be fit_archetypes'
    output)."""
    columns = columns or FEATURE_COLUMNS
    grouped = df.groupby(["cluster", "cluster_label"])
    profile = grouped[columns].mean()
    profile["count"] = grouped.size()
    return profile.reset_index()
