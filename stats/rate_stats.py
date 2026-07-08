"""Standard rate stats derived from season counting-stat aggregates.

All functions guard divide-by-zero and return None rather than raising —
short amateur-league seasons produce real edge cases (a pitcher who faced
one batter, a pinch-runner with zero plate appearances).
"""

from typing import Any


def outs_to_ip(outs_recorded: int) -> float:
    return outs_recorded / 3.0


def outs_to_ip_display(outs_recorded: int) -> float:
    """IP in baseball's .0/.1/.2 notation (thirds of an inning as tenths),
    not a true decimal fraction — for display only, never for arithmetic."""
    return (outs_recorded // 3) + (outs_recorded % 3) / 10


def total_bases(h: int, doubles: int, triples: int, hr: int) -> int:
    singles = h - doubles - triples - hr
    return singles + 2 * doubles + 3 * triples + 4 * hr


def avg(h: int, ab: int) -> float | None:
    return h / ab if ab else None


def obp(h: int, bb: int, hbp: int, ab: int, sf: int) -> float | None:
    denom = ab + bb + hbp + sf
    return (h + bb + hbp) / denom if denom else None


def slg(h: int, doubles: int, triples: int, hr: int, ab: int) -> float | None:
    if not ab:
        return None
    return total_bases(h, doubles, triples, hr) / ab


def fielding_pct(po: int, a: int, e: int) -> float | None:
    denom = po + a + e
    return (po + a) / denom if denom else None


def avg_risp(risp_h: int, risp_ab: int) -> float | None:
    return risp_h / risp_ab if risp_ab else None


def fps_pct(fps_strikes: int, fps_pa: int) -> float | None:
    return fps_strikes / fps_pa if fps_pa else None


def batting_rate_stats(row: Any) -> dict[str, float | None]:
    """row: a BattingSeasonStats instance or any object/dict with the same
    field names (ab, h, doubles, triples, hr, bb, hbp, so, sf, pa)."""
    ab, h = row.ab, row.h
    doubles, triples, hr = row.doubles, row.triples, row.hr
    bb, hbp, so, sf, pa = row.bb, row.hbp, row.so, row.sf, row.pa

    avg_ = avg(h, ab)
    obp_ = obp(h, bb, hbp, ab, sf)
    slg_ = slg(h, doubles, triples, hr, ab)
    ops_ = (obp_ + slg_) if obp_ is not None and slg_ is not None else None
    iso_ = (slg_ - avg_) if slg_ is not None and avg_ is not None else None

    return {
        "avg": avg_,
        "obp": obp_,
        "slg": slg_,
        "ops": ops_,
        "iso": iso_,
        "bb_pct": (bb / pa) if pa else None,
        "k_pct": (so / pa) if pa else None,
    }


def hit_type_mix(row: Any) -> dict[str, float] | None:
    """row: a BattingSeasonStats instance or equivalent (h, doubles, triples,
    hr). Returns each hit type as a fraction of total hits, for use as
    batter-archetype clustering features — None when the player has no hits
    to compute a mix from."""
    if not row.h:
        return None
    singles = row.h - row.doubles - row.triples - row.hr
    return {
        "singles_pct": singles / row.h,
        "doubles_pct": row.doubles / row.h,
        "triples_pct": row.triples / row.h,
        "hr_pct": row.hr / row.h,
    }


def pitching_rate_stats(row: Any) -> dict[str, float | None]:
    """row: a PitchingSeasonStats instance or equivalent (outs_recorded, h,
    r, er, bb, so)."""
    ip = outs_to_ip(row.outs_recorded)
    if not ip:
        return {"ip": 0.0, "era": None, "whip": None, "k9": None, "bb9": None}

    return {
        "ip": ip,
        "era": (row.er * 9) / ip,
        "whip": (row.bb + row.h) / ip,
        "k9": (row.so * 9) / ip,
        "bb9": (row.bb * 9) / ip,
    }
