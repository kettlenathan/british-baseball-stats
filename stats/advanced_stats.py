"""Advanced stats built on top of rate_stats + league_context.

wRC+ and ERA+ are simplified relative to their real definitions: no park
factor is applied (fixed at neutral / 1.0) since no park-factor data exists
for this league — documented here and surfaced in the UI wherever these
appear.
"""

from typing import Any

from stats import constants
from stats.rate_stats import outs_to_ip


def woba(row: Any) -> float | None:
    """row: a BattingSeasonStats instance (ab, bb, ibb, hbp, h, doubles,
    triples, hr, sf)."""
    denom = row.ab + row.bb - row.ibb + row.sf + row.hbp
    if not denom:
        return None
    singles = row.h - row.doubles - row.triples - row.hr
    numerator = (
        constants.WOBA_WEIGHT_UBB * (row.bb - row.ibb)
        + constants.WOBA_WEIGHT_HBP * row.hbp
        + constants.WOBA_WEIGHT_1B * singles
        + constants.WOBA_WEIGHT_2B * row.doubles
        + constants.WOBA_WEIGHT_3B * row.triples
        + constants.WOBA_WEIGHT_HR * row.hr
    )
    return numerator / denom


def wrc_plus(player_woba: float | None, lg_woba: float | None) -> float | None:
    """Simplified — no park factor (fixed at neutral), unlike real wRC+."""
    if player_woba is None or not lg_woba:
        return None
    return 100 * (player_woba / lg_woba)


def fip(row: Any, fip_constant: float | None) -> float | None:
    """row: a PitchingSeasonStats instance (outs_recorded, hr, bb, hbp, so)."""
    ip = outs_to_ip(row.outs_recorded)
    if not ip or fip_constant is None:
        return None
    return (
        constants.FIP_WEIGHT_HR * row.hr
        + constants.FIP_WEIGHT_BB_HBP * (row.bb + row.hbp)
        - constants.FIP_WEIGHT_SO * row.so
    ) / ip + fip_constant


def era_plus(player_era: float | None, lg_era: float | None) -> float | None:
    """Simplified — no park factor (fixed at neutral), unlike real ERA+."""
    if not player_era or lg_era is None:
        return None
    return 100 * (lg_era / player_era)
