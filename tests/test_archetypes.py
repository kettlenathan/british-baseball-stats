import pandas as pd
import pytest
from sklearn.preprocessing import StandardScaler

from stats.archetypes import (
    FEATURE_COLUMNS,
    _label_dominant_features,
    _prepare_features,
    cluster_profile,
    fit_archetypes,
    select_k,
)


def _synthetic_batters() -> pd.DataFrame:
    """Two obviously-separable synthetic groups (power pull hitters vs.
    patient opposite-field hitters), with small per-row jitter that's tiny
    relative to the gap between groups."""
    rows = []
    for i in range(10):
        rows.append(
            {
                "player": f"PowerPull{i}",
                "true_group": "A",
                "pull_pct": 0.50 + 0.005 * i,
                "center_pct": 0.30,
                "oppo_pct": 0.20 - 0.005 * i,
                "iso": 0.250 + 0.002 * i,
                "bb_pct": 0.06 + 0.001 * i,
                "k_pct": 0.20,
                "singles_pct": 0.40,
                "doubles_pct": 0.20,
                "triples_pct": 0.02,
                "hr_pct": 0.25 + 0.002 * i,
            }
        )
    for i in range(10):
        rows.append(
            {
                "player": f"PatientOppo{i}",
                "true_group": "B",
                "pull_pct": 0.20 + 0.005 * i,
                "center_pct": 0.30,
                "oppo_pct": 0.50 - 0.005 * i,
                "iso": 0.090 + 0.002 * i,
                "bb_pct": 0.15 + 0.001 * i,
                "k_pct": 0.12,
                "singles_pct": 0.55,
                "doubles_pct": 0.10,
                "triples_pct": 0.01,
                "hr_pct": 0.05 + 0.002 * i,
            }
        )
    return pd.DataFrame(rows)


def test_select_k_picks_two_obviously_separable_groups():
    df = _synthetic_batters()
    X = StandardScaler().fit_transform(_prepare_features(df)[FEATURE_COLUMNS])
    best_k, diagnostics = select_k(X, range(2, 5))
    assert best_k == 2
    assert list(diagnostics.columns) == ["k", "inertia", "silhouette"]
    assert len(diagnostics) == 3


def test_fit_archetypes_recovers_synthetic_groups():
    df = _synthetic_batters()
    result = fit_archetypes(df, k=2)

    assert set(result["cluster"]) == {0, 1}
    # Each true group maps cleanly onto exactly one fitted cluster — check
    # via crosstab rather than asserting raw cluster-id numbers, since
    # k-means' 0/1 labeling order isn't guaranteed to match group ordering.
    crosstab = pd.crosstab(result["true_group"], result["cluster"])
    assert (crosstab.max(axis=1) == 10).all()
    assert (crosstab.min(axis=1) == 0).all()


def test_fit_archetypes_auto_selects_k_when_not_given():
    df = _synthetic_batters()
    result = fit_archetypes(df)
    assert result["cluster"].nunique() == 2


def test_feature_columns_excludes_redundant_signals():
    # iso is a recombination of doubles/triples/hr_pct; center_pct and
    # singles_pct are each the compositional "remainder" of their group;
    # pull_pct/oppo_pct are collapsed into one signed pull_minus_oppo
    # feature since together they only carry one axis of real variation.
    # All would double-count a signal another feature already carries.
    assert "iso" not in FEATURE_COLUMNS
    assert "center_pct" not in FEATURE_COLUMNS
    assert "singles_pct" not in FEATURE_COLUMNS
    assert "pull_pct" not in FEATURE_COLUMNS
    assert "oppo_pct" not in FEATURE_COLUMNS
    assert "pull_minus_oppo" in FEATURE_COLUMNS


def test_prepare_features_computes_net_pull():
    df = pd.DataFrame({"pull_pct": [0.5, 0.2], "oppo_pct": [0.2, 0.5]})
    prepared = _prepare_features(df)
    assert prepared["pull_minus_oppo"].tolist() == [pytest.approx(0.3), pytest.approx(-0.3)]


def test_label_dominant_features_names_top_features():
    weights = pd.Series({col: 0.0 for col in FEATURE_COLUMNS})
    weights["hr_pct"] = 2.0
    weights["pull_minus_oppo"] = -1.5
    label = _label_dominant_features(weights, top_n=2)
    assert "High HR%" in label
    assert "Low Net Pull%" in label


def test_fit_archetypes_labels_pca_axes():
    df = _synthetic_batters()
    result = fit_archetypes(df, k=2)
    assert result["pc1_label"].nunique() == 1
    assert result["pc2_label"].nunique() == 1
    assert result["pc1_label"].iloc[0] != result["pc2_label"].iloc[0]


def test_cluster_profile_counts_and_shape():
    df = _synthetic_batters()
    result = fit_archetypes(df, k=2)
    profile = cluster_profile(result)
    assert set(profile.columns) >= {"cluster", "cluster_label", "count", *FEATURE_COLUMNS}
    assert profile["count"].sum() == len(df)
    assert len(profile) == 2


def test_cluster_profile_accepts_broader_descriptive_columns():
    df = _synthetic_batters()
    result = fit_archetypes(df, k=2)
    profile = cluster_profile(result, columns=[*FEATURE_COLUMNS, "iso", "singles_pct"])
    assert {"iso", "singles_pct"} <= set(profile.columns)
